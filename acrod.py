import datetime
import io
from collections import OrderedDict
import json
import math
import mmap
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import types
import uuid
import weakref
import zlib
from dataclasses import dataclass, asdict, field, replace
from functools import lru_cache

from PySide6.QtCore import Qt, QTimer, QThread, Signal, QUrl, QLockFile, QByteArray, QObject, QSize, QEvent, QMimeData, QPoint, QRect, QRectF, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import (QColor, QPalette, QDesktopServices, QKeySequence, QAction,
                           QImage, QPixmap, QPainter, QTransform, QPen, QIcon, QDrag, QShortcut, QFont)
from PySide6.QtWidgets import (
    QStyle,
    QLayout,
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QTextEdit, QFileDialog, QMessageBox,
    QFrame, QTabWidget, QTabBar, QRadioButton, QButtonGroup, QCheckBox, QComboBox,
    QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QInputDialog, QSplitter, QScrollArea, QProgressBar,
    QMenu, QDialog, QDialogButtonBox,
    QFormLayout, QPlainTextEdit, QListWidget, QListWidgetItem, QToolButton, QSlider,
    QAbstractScrollArea, QStackedWidget, QSizePolicy
)

# ---------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------
APP_NAME = "Acro-Data"
APP_SHORT = "AcroD"          # exe name, state folder, file prefixes
APP_VERSION = "1.12.1"
APP_OWNER = "Donnie Harding (DHSeaDev)"
APP_COPYRIGHT = f"\u00a9 2026 {APP_OWNER}. Released under the MIT License."
APP_NOTICE = "Free software, provided as is, without warranty."

SPLIT_DIR_NAME = "Split"
OPTIMIZED_DIR_NAME = "Optimized"
ORGANIZED_DIR_NAME = "Reorganized"

# Operator-adjustable values (Settings tab > Advanced). Everything here has
# a default that matches the behaviour before it was adjustable, so a blank
# ui_state.json runs exactly as v8 did. Read through setting(), never
# directly - the getter validates and falls back, so a hand-edited file
# cannot produce an empty folder name or a zero undo depth.
ADVANCED_DEFAULTS = {
    "split_dir_name": SPLIT_DIR_NAME,
    "optimized_dir_name": OPTIMIZED_DIR_NAME,
    "organized_dir_name": ORGANIZED_DIR_NAME,
    "organizer_replace_original": False,  # Page Organizer: replace file, keep a backup
    "organizer_keep_scripts": False,      # Page Organizer: keep the file's own scripts and launch actions on save (pasted pages are always cleaned)
    "organizer_thumb_size": 150,          # Page Organizer thumbnail width, 100-260 px
    "organizer_undo_depth": 100,          # Page Organizer page changes that can be undone, 5-200
    "organizer_extract_delete_default": False,  # Extract: "delete pages after extracting" starts ticked
    "organizer_auto_refresh": "focus",    # PDF list: "never" | "focus" (on return to the window) | "timer" (also every 10 s)
    "reader_default_zoom": "width",       # Page Organizer reader: "width" | "page" | "100"
    "reader_cache_mb": 256,               # Page Organizer reader: memory for drawn pages, 64-1024 MB
    "cover_suffix": "_Cover",
    "remaining_suffix": "_Remaining",
    "merge_backups": 1,          # copies of overwritten files kept, 1-10
    "undo_depth": 1,             # rename/move runs that can be undone, 1-20
    "default_tab": -1,           # -1 = the tab you were on last time
    "mover_separators": "",      # "" = any character that is not a letter/digit
    "ceiling_presets": [["Default", 6.9]],   # [name, MB] rows for the Optimizer
    "optimizer_flatten_dpi": 120,        # DPI when pages become pictures, 72-300
    "optimizer_allow_flatten": True,     # last-resort flatten permitted
    "optimizer_open_when_done": False,   # open the Optimized folder after a run
    "mascot_dialogs": True,              # Pagey in confirmation / result boxes
    "ui_layout": "tray",                 # "tray" (file tray + tool buttons) | "classic" (the tab strip)
    "actions_side": "right",             # which side of each tool the Actions column sits on: "right" | "left"
    "actions_collapsed": False,          # the Actions column shrunk to a thin rail (Ctrl+J)
    "launch_reader_full": True,          # a PDF opened from Windows fills the screen in the Page Organizer's reader
}
AUTO_REFRESH_CHOICES = (("never", "Never (use Refresh or F5)"),
                        ("focus", "When I return to the window or tab"),
                        ("timer", "Also every 10 seconds"))
READER_ZOOM_CHOICES = (("width", "Fit width"), ("page", "Fit the whole page"), ("100", "100%"))
_BAD_NAME_CHARS = set('<>:"/\\|?*')


_RESERVED_NAMES = re.compile(
    r"(?i)^(CON|PRN|AUX|NUL|CLOCK\$|COM[0-9\u00b9\u00b2\u00b3]|LPT[0-9\u00b9\u00b2\u00b3])(\..*)?$")


def valid_name_component(text) -> bool:
    """One folder- or suffix-worth of filename: no separators, no reserved
    characters or device names, no trailing dot or space (Windows silently
    strips those), under NTFS's 255-character component limit."""
    if not isinstance(text, str) or not text.strip():
        return False
    if len(text) > 255:
        return False
    if text != text.strip() or text.endswith("."):
        return False
    if any(c in _BAD_NAME_CHARS or ord(c) < 32 for c in text):
        return False
    if _RESERVED_NAMES.match(text):
        return False
    return text not in (".", "..")

# Hard ceiling for the PDF Optimizer, in megabytes. Operator-set.
DEFAULT_SIZE_CEILING_MB = 6.9

# Resolution used when pages are re-rendered as images, whether as the
# ladder's last resort or via the force-flatten tick box.
DEFAULT_FLATTEN_DPI = 120

# Layout constants (theme-independent)
DROPZONE_STRETCH = 3
SPACER_STRETCH = 1
DROPZONE_MIN_H = 150
CONTROL_PANE_W = 420
#: Bulk Renamer split, as a fraction of the pane. A fixed 420px control column
#: was fine when it held a find/replace form; with Auto Rename's keyword table
#: in it the two columns that matter were squeezed to an ellipsis. 40/60 gives
#: the controls room and still leaves the preview the larger share.
RENAMER_SPLIT_LEFT = 0.40
#: Bumped when the DEFAULT split changes. A saved position from an older
#: version is ignored once, otherwise everyone who has ever dragged the
#: divider - or just opened the tab - keeps the old default forever and the
#: new one looks like it did nothing.
RENAMER_SPLIT_KEY = "renamer_splitter_v2"

FONT_STACK = "'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif"
FONT_MONO = "Consolas, 'Cascadia Mono', monospace"

TEXT_SIZES = {"Small": 11, "Normal": 12, "Large": 14}
DEFAULT_TEXT_SIZE = "Normal"

# ---------------------------------------------------------------------
# Themes
#
# Every colour the app draws comes from one of these dicts. Nothing is
# hardcoded in a widget, so a theme switch is total. Each palette is
# contrast-checked in the test suite against the same thresholds.
# ---------------------------------------------------------------------
THEMES = {
    "Vermilion": {
        "bg": "#f4f2ef", "panel": "#ffffff", "border": "#3b3b3f",
        "text": "#26262a", "muted": "#5a5a60",
        "accent": "#bf3a1f", "accent_hover": "#a02e17", "accent_pressed": "#852511",
        "on_accent": "#ffffff",
        "red": "#b02a37", "green": "#1a7f37",
        "amber": "#8c4600",
        "row_alt": "#faf8f6", "row_hover": "#f4eeea", "select_bg": "#f9e4dd",
        "header_bg": "#e6e3df", "grid": "#d6d2cd",
        "disabled_bg": "#dedbd7", "disabled_text": "#5f5c62", "disabled_border": "#bab5b0",
        "drop_active": "#faebe6",
        "scroll": "#bab5b0",
        "tooltip_bg": "#26262a", "tooltip_text": "#ffffff",
    },
    "Light": {
        "bg": "#f2f3f5", "panel": "#ffffff", "border": "#4a4a4a",
        "text": "#1a1a1a", "muted": "#5c5c5c",
        "accent": "#0052CC", "accent_hover": "#003d99", "accent_pressed": "#00297a",
        "on_accent": "#ffffff",
        "red": "#c0392b", "green": "#1a7f37",
        "amber": "#8c4600",
        "row_alt": "#f7f8fa", "row_hover": "#eef3fd", "select_bg": "#cfe0fb",
        "header_bg": "#e9ebef", "grid": "#d5d8dd",
        "disabled_bg": "#dcdfe4", "disabled_text": "#6f747c", "disabled_border": "#b4b9c0",
        "drop_active": "#e6edfb",
        "scroll": "#b4b9c0",
        "tooltip_bg": "#1a1a1a", "tooltip_text": "#ffffff",
    },
    "Dark": {
        "bg": "#161a22", "panel": "#1e2430", "border": "#3a4354",
        "text": "#e6e9ef", "muted": "#9aa3b2",
        "accent": "#5B8DEF", "accent_hover": "#7BA5F5", "accent_pressed": "#3F73D6",
        "on_accent": "#0b1220",
        "red": "#ff8a80", "green": "#6ee7a0",
        "amber": "#ffcc66",
        "row_alt": "#222a38", "row_hover": "#2b3648", "select_bg": "#31456b",
        "header_bg": "#2a3242", "grid": "#2f3849",
        "disabled_bg": "#2a3140", "disabled_text": "#8b95a7", "disabled_border": "#3a4354",
        "drop_active": "#233049",
        "scroll": "#3a4354",
        "tooltip_bg": "#e6e9ef", "tooltip_text": "#161a22",
    },
    "Periwinkle": {
        "bg": "#f0eefb", "panel": "#ffffff", "border": "#8079b8",
        "text": "#241f3d", "muted": "#5d5680",
        "accent": "#5B54B8", "accent_hover": "#4a44a0", "accent_pressed": "#3a3583",
        "on_accent": "#ffffff",
        "red": "#b02a37", "green": "#1a7f37",
        "amber": "#8c4600",
        "row_alt": "#f7f5fd", "row_hover": "#eae7f9", "select_bg": "#d9d4f3",
        "header_bg": "#e4e0f6", "grid": "#d3cee9",
        "disabled_bg": "#ddd9ee", "disabled_text": "#736d94", "disabled_border": "#b8b2d6",
        "drop_active": "#e4e0f6",
        "scroll": "#b8b2d6",
        "tooltip_bg": "#241f3d", "tooltip_text": "#ffffff",
    },
}
DEFAULT_THEME = "Vermilion"

# The palette currently in force. Widgets that must set a colour from
# Python (the preview table's status column) read it from here rather than
# from a module constant, so a theme switch reaches them too.
_CURRENT = dict(THEMES[DEFAULT_THEME])


def theme_color(key: str) -> str:
    return _CURRENT.get(key, "#000000")


def _checkable_icon(kind: str, colour: str, size: int = 18) -> str:
    """
    Draw the tick / radio dot to a PNG and return a QSS-safe path.

    Needed because styling QCheckBox::indicator at all makes Qt stop drawing
    its NATIVE tick - verified by rendering: a styled indicator with no
    image: rule shows an empty box in the checked state. QSS cannot draw a
    tick, so one is painted here and referenced by url().

    Returns "" on any failure; the caller then omits the image: rule and the
    checked state still reads clearly as a solid accent fill.
    """
    try:
        from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QBrush
        name = f"ind_{kind}_{colour.lstrip('#')}_{size}.png"
        path = os.path.join(_log_dir(), name)
        if not os.path.isfile(path):
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.Antialiasing)
            if kind == "dot":
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(QColor(colour)))
                r = size * 0.27
                painter.drawEllipse(int(size / 2 - r), int(size / 2 - r),
                                    int(r * 2), int(r * 2))
            else:
                pen = QPen(QColor(colour))
                pen.setWidth(max(2, size // 6))
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                painter.setPen(pen)
                painter.drawLine(int(size * .22), int(size * .52),
                                 int(size * .42), int(size * .72))
                painter.drawLine(int(size * .42), int(size * .72),
                                 int(size * .78), int(size * .28))
            painter.end()
            if not pm.save(path, "PNG"):
                return ""
        # QSS url() wants forward slashes even on Windows.
        return path.replace(chr(92), "/")   # QSS url() wants forward slashes
    except Exception:
        return ""


def _chevron_icon(direction: str, colour: str, size: int = 24) -> str:
    """Drop-down / spin-box chevron as a PNG, same mechanism (and same reason)
    as _checkable_icon: a styled combo or spin box loses its native arrow, and
    QSS cannot draw one, so one is painted here and referenced by url()."""
    try:
        from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QPainterPath
        path = os.path.join(_log_dir(), f"chev_{direction}_{colour.lstrip('#')}_{size}.png")
        if not os.path.isfile(path):
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.Antialiasing)
            pen = QPen(QColor(colour))
            pen.setWidthF(size / 8.0)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            y0, y1 = (0.36, 0.64) if direction == "down" else (0.64, 0.36)
            pp = QPainterPath()
            pp.moveTo(size * 0.22, size * y0)
            pp.lineTo(size * 0.5, size * y1)
            pp.lineTo(size * 0.78, size * y0)
            painter.drawPath(pp)
            painter.end()
            if not pm.save(path, "PNG"):
                return ""
        return path.replace(chr(92), "/")
    except Exception:
        return ""


def field_style(p: dict) -> str:
    """Combo boxes and spin boxes, styled to match the rounded line edits.
    The old comment here said not to - and was right for a sub-control styled
    WITHOUT an image (measured: flat box). Every arrow below has one."""
    img = (lambda path: f"image: url({path}); width: 12px; height: 12px;" if path else "")
    dn, up = _chevron_icon("down", p["text"]), _chevron_icon("up", p["text"])
    dn_off, up_off = (_chevron_icon("down", p["disabled_text"]),
                      _chevron_icon("up", p["disabled_text"]))
    return f"""
QComboBox, QSpinBox {{
    background-color: {p['panel']}; color: {p['text']};
    border: 1px solid {p['border']}; border-radius: 6px;
    padding: 4px 8px; min-height: 22px;
    selection-background-color: {p['accent']}; selection-color: {p['on_accent']};
}}
QComboBox {{ padding-right: 30px; }}
QSpinBox {{ padding-right: 26px; }}
QComboBox:hover, QSpinBox:hover {{ border-color: {p['accent']}; }}
QComboBox:focus, QComboBox:on, QSpinBox:focus {{
    border: 2px solid {p['accent']}; padding-top: 3px; padding-bottom: 3px; padding-left: 7px;
}}
QComboBox:focus, QComboBox:on {{ padding-right: 29px; }}
QSpinBox:focus {{ padding-right: 25px; }}
QComboBox:disabled, QSpinBox:disabled {{
    background-color: {p['disabled_bg']}; color: {p['disabled_text']};
    border: 1px solid {p['disabled_border']};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 26px; border: none; border-left: 1px solid {p['grid']};
    border-top-right-radius: 5px; border-bottom-right-radius: 5px;
    background-color: {p['header_bg']};
}}
QComboBox::drop-down:hover {{ background-color: {p['row_hover']}; }}
QComboBox::drop-down:disabled {{ background-color: {p['disabled_bg']}; }}
QComboBox::down-arrow {{ {img(dn)} }}
QComboBox::down-arrow:disabled {{ {img(dn_off)} }}
QComboBox QAbstractItemView {{
    background-color: {p['panel']}; color: {p['text']};
    border: 1px solid {p['border']}; border-radius: 4px; outline: 0; padding: 2px;
    selection-background-color: {p['accent']}; selection-color: {p['on_accent']};
}}
QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 0px 6px; border-radius: 3px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border; width: 22px;
    border: none; border-left: 1px solid {p['grid']}; background-color: {p['header_bg']};
}}
QSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 5px; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 5px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background-color: {p['row_hover']}; }}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{ background-color: {p['select_bg']}; }}
QSpinBox::up-arrow {{ {img(up)} }}
QSpinBox::down-arrow {{ {img(dn)} }}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {{ {img(up_off)} }}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {{ {img(dn_off)} }}
"""


def checkable_style(p: dict) -> str:
    """
    Check boxes and radio buttons.

    Three things were measured by rendering the widget offscreen and LOOKING
    at the result, not reasoned about:

      1. The native indicator is too small to read at a glance. It is now
         18px with a 2px border.
      2. Qt draws a focus RECTANGLE around the whole row - label included.
         `outline: none` does NOT remove it; `border: none` on the widget
         does. The keyboard cue moved onto the indicator instead, so focus is
         still visible without a box around the text.
      3. QCheckBox and QRadioButton must be given SEPARATE rules. Written as
         one combined selector (`QCheckBox, QRadioButton::indicator`) Qt put
         the indicator's border back on the whole row.
    """
    tick = _checkable_icon("tick", p["on_accent"])
    dot = _checkable_icon("dot", p["on_accent"])
    tick_off = _checkable_icon("tick", p["disabled_text"])
    img = (lambda path: f"image: url({path});" if path else "")
    return f"""
QCheckBox {{ border: none; outline: none; }}
QRadioButton {{ border: none; outline: none; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border: 2px solid {p['border']};
    background-color: {p['panel']}; border-radius: 3px;
}}
QRadioButton::indicator {{
    width: 18px; height: 18px; border: 2px solid {p['border']};
    background-color: {p['panel']}; border-radius: 11px;
}}
QCheckBox::indicator:hover {{
    border-color: {p['accent']}; background-color: {p['drop_active']};
}}
QRadioButton::indicator:hover {{
    border-color: {p['accent']}; background-color: {p['drop_active']};
}}
QCheckBox::indicator:focus {{
    border-color: {p['accent']}; background-color: {p['drop_active']};
}}
QRadioButton::indicator:focus {{
    border-color: {p['accent']}; background-color: {p['drop_active']};
}}
QCheckBox::indicator:checked {{
    background-color: {p['accent']}; border-color: {p['accent']}; {img(tick)}
}}
QRadioButton::indicator:checked {{
    background-color: {p['accent']}; border-color: {p['accent']}; {img(dot)}
}}
QCheckBox::indicator:disabled {{
    border-color: {p['disabled_border']}; background-color: {p['disabled_bg']};
}}
QRadioButton::indicator:disabled {{
    border-color: {p['disabled_border']}; background-color: {p['disabled_bg']};
}}
QCheckBox::indicator:checked:disabled {{
    background-color: {p['disabled_border']};
    border-color: {p['disabled_border']}; {img(tick_off)}
}}
"""


def _rgba(hex_colour: str, alpha: float) -> str:
    """A QSS rgba() from a #rrggbb palette colour."""
    h = hex_colour.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{int(alpha * 255)})"


def build_style(p: dict, fs_base: int) -> str:
    """Render the full stylesheet for one palette at one text size."""
    fs_small = max(9, fs_base - 1)
    fs_header = fs_base + 8
    return f"""
QWidget {{
    font-family: {FONT_STACK};
    font-size: {fs_base}px;
    color: {p['text']};
}}
QMainWindow, QDialog {{
    background-color: {p['bg']};
}}
QScrollArea {{ background: transparent; border: none; }}
QSplitter::handle {{ background-color: {p['bg']}; }}
QSplitter::handle:hover {{ background-color: {p['accent']}; }}

QTabWidget::pane {{
    border: none;
    background-color: {p['bg']};
}}
QTabBar {{ background-color: {p['panel']}; }}
QTabBar::tab {{
    background-color: transparent;
    color: {p['muted']};
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 2px;
    padding: 10px 16px;
    font-weight: 600;
    font-size: {fs_base}px;
}}
QTabBar[density="tight"]::tab {{ padding: 10px 10px; }}
QTabBar::tab:hover:!selected {{
    background-color: {p['row_hover']};
    color: {p['text']};
}}
QTabBar::tab:selected {{
    background-color: {p['bg']};
    color: {p['text']};
    border-bottom: 2px solid {p['accent']};
}}

QLabel#header {{ color: {p['text']}; font-size: {fs_header}px; font-weight: 700; }}
QFrame#inspector {{ background-color: {p['panel']}; border: none; border-left: 1px solid {p['grid']}; border-radius: 0; }}
QFrame#inspector[side="left"] {{ border-left: none; border-right: 1px solid {p['grid']}; }}
QFrame#inspector QPushButton {{ text-align: center; }}
QPushButton#railToggle {{ background: transparent; border: none; border-radius: 8px; color: {p['muted']}; padding: 2px 6px; font-weight: 600; }}
QPushButton#railToggle:hover {{ background-color: {_rgba(p['accent'], 0.14)}; color: {p['accent']}; }}
QLabel#sectionTitle {{
    color: {p['text']}; font-size: {fs_base}px; font-weight: 700; letter-spacing: 0.5px;
}}
QLabel#pathLabel {{ color: {p['muted']}; font-size: {fs_small}px; }}
QLabel#status {{ color: {p['muted']}; font-size: {fs_small}px; }}
QLabel#status[kind="ok"]    {{ color: {p['green']}; }}
QLabel#status[kind="error"] {{ color: {p['red']}; }}
QLabel#status[kind="busy"]  {{ color: {p['accent']}; }}
/* Advisory, not an error: something legal that is probably a mistake. Distinct
   from red so a warning does not read as a blocked batch. */
QLabel#status[kind="warn"]  {{ color: {p['amber']}; }}
QLabel#faqQ {{ font-weight: 700; color: {p['text']}; }}
QLabel#faqA {{ color: {p['muted']}; }}
QLabel#legal {{ color: {p['muted']}; font-size: {fs_small}px; }}
QLabel#hint {{ color: {p['muted']}; font-size: {fs_small}px; }}
QLabel#peek {{ color: {p['muted']}; font-size: {fs_small}px; font-family: {FONT_MONO}; }}

QFrame#panel {{ background-color: {p['panel']}; border: 1px solid {p['grid']}; border-radius: 8px; }}

QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {p['panel']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 6px;
    font-size: {fs_base}px;
    selection-background-color: {p['accent']};
    selection-color: {p['on_accent']};
}}
QTextEdit {{ font-family: {FONT_MONO}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 2px solid {p['accent']};
    padding: 5px;
}}
QLineEdit:disabled, QTextEdit:disabled {{
    background-color: {p['disabled_bg']};
    color: {p['disabled_text']};
    border: 1px solid {p['disabled_border']};
}}

QPushButton {{
    background-color: {p['accent']};
    color: {p['on_accent']};
    border: 1px solid {p['accent_pressed']};
    border-radius: 6px;
    padding: 8px 14px;
    font-weight: 600;
    font-size: {fs_base}px;
}}
QPushButton:hover {{ background-color: {p['accent_hover']}; }}
QPushButton:pressed {{ background-color: {p['accent_pressed']}; }}
QPushButton:focus {{ border: 2px solid {p['text']}; padding: 7px 13px; }}
QPushButton:disabled {{
    background-color: {p['disabled_bg']};
    color: {p['disabled_text']};
    border: 1px solid {p['disabled_border']};
}}
QPushButton#primaryAction {{ font-size: {fs_base + 2}px; padding: 12px; }}
QPushButton#primaryAction:disabled {{
    background-color: {p['disabled_bg']};
    color: {p['disabled_text']};
    border: 1px dashed {p['disabled_border']};
}}
QPushButton#secondary {{
    background-color: {p['header_bg']};
    color: {p['text']};
    border: 1px solid {p['border']};
    font-weight: 600;
}}
QPushButton#secondary:hover {{ background-color: {p['row_hover']}; }}
QPushButton#secondary:pressed {{ background-color: {p['select_bg']}; }}
QPushButton#secondary:disabled {{
    background-color: {p['panel']};
    color: {p['disabled_text']};
    border: 1px solid {p['disabled_border']};
}}

QCheckBox, QRadioButton {{
    color: {p['text']}; font-size: {fs_base}px; spacing: 8px; padding: 4px 0px;
}}
{checkable_style(p)}
QTabBar::tab:focus {{
    border: 2px solid {p['accent']};
}}
QTableWidget:focus, QAbstractScrollArea:focus {{
    border: 2px solid {p['accent']};
}}
QLabel#dropZone:focus {{
    border: 3px solid {p['accent']};
    background-color: {p['drop_active']};
}}
QCheckBox:disabled, QRadioButton:disabled {{ color: {p['disabled_text']}; }}

{field_style(p)}

QListWidget {{
    background-color: {p['panel']};
    color: {p['text']};
    border: 1px solid {p['border']};
    font-size: {fs_base}px;
    outline: none;
}}
QListWidget::item {{ padding: 4px 5px; border: 2px solid transparent; }}
QListWidget::item:hover {{ background-color: {p['row_hover']}; }}
QListWidget::item:selected {{
    background-color: {p['select_bg']};
    color: {p['text']};
    border: 2px solid {p['accent']};
}}
QTableWidget {{
    background-color: {p['panel']};
    alternate-background-color: {p['row_alt']};
    color: {p['text']};
    border: 1px solid {p['border']};
    gridline-color: {p['grid']};
    font-size: {fs_base}px;
    selection-background-color: {p['select_bg']};
    selection-color: {p['text']};
}}
QTableWidget::item {{ padding: 4px 6px; border: none; }}
QTableWidget::item:hover {{ background-color: {p['row_hover']}; }}
QHeaderView::section {{
    background-color: {p['header_bg']};
    color: {p['text']};
    border: none;
    border-right: 1px solid {p['border']};
    border-bottom: 1px solid {p['border']};
    padding: 6px;
    font-weight: 700;
    font-size: {fs_small}px;
}}
QTableCornerButton::section {{ background-color: {p['header_bg']}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 14px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['scroll']}; min-height: 36px; border-radius: 5px; }}
QScrollBar::handle:vertical:hover {{ background: {p['accent']}; }}
QScrollBar::handle:vertical:pressed {{ background: {p['accent_pressed']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; width: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QScrollBar:horizontal {{ background: transparent; height: 14px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p['scroll']}; min-width: 36px; border-radius: 5px; }}
QScrollBar::handle:horizontal:hover {{ background: {p['accent']}; }}
QScrollBar::handle:horizontal:pressed {{ background: {p['accent_pressed']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; height: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}

QProgressBar {{
    background-color: {p['panel']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    color: {p['text']};
    font-size: {fs_small}px;
    text-align: center;
    min-height: 18px;
}}
QProgressBar::chunk {{ background-color: {p['accent']}; border-radius: 5px; }}

QLabel#dropZone {{
    background-color: {p['panel']};
    border: 2px dashed {p['accent']};
    border-radius: 12px;
    color: {p['accent']};
    font-size: {fs_base}px;
    font-weight: 600;
}}
QLabel#dropZone[dragActive="true"] {{ background-color: {p['drop_active']}; }}

QToolTip {{
    background-color: {p['tooltip_bg']};
    color: {p['tooltip_text']};
    border: 1px solid {p['border']};
    padding: 5px;
    font-size: {fs_small}px;
}}
"""


def build_palette(p: dict):
    """A QPalette matching the theme.

    MEASURED, not assumed: QStyleHints.setColorScheme() had no effect on the
    indicator glyphs, but setPalette() did. Native-drawn parts of a control
    (the checkbox tick, the spin arrows, the combo arrow) follow the palette,
    not the stylesheet — without this, dark mode ships with light checkboxes.
    """
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(p["bg"]))
    pal.setColor(QPalette.WindowText, QColor(p["text"]))
    pal.setColor(QPalette.Base, QColor(p["panel"]))
    pal.setColor(QPalette.AlternateBase, QColor(p["row_alt"]))
    pal.setColor(QPalette.Text, QColor(p["text"]))
    pal.setColor(QPalette.Button, QColor(p["panel"]))
    pal.setColor(QPalette.ButtonText, QColor(p["text"]))
    pal.setColor(QPalette.Highlight, QColor(p["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor(p["on_accent"]))
    pal.setColor(QPalette.ToolTipBase, QColor(p["tooltip_bg"]))
    pal.setColor(QPalette.ToolTipText, QColor(p["tooltip_text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(p["muted"]))
    for grp in (QPalette.Disabled,):
        pal.setColor(grp, QPalette.Text, QColor(p["disabled_text"]))
        pal.setColor(grp, QPalette.ButtonText, QColor(p["disabled_text"]))
        pal.setColor(grp, QPalette.WindowText, QColor(p["disabled_text"]))
    return pal


def apply_theme(app, theme_name: str = None, size_name: str = None) -> None:
    """Apply a theme + text size to the whole application."""
    global _CURRENT
    if theme_name is None or size_name is None:
        state = load_ui_state()
        theme_name = theme_name or state.get("theme", DEFAULT_THEME)
        size_name = size_name or state.get("text_size", DEFAULT_TEXT_SIZE)
    if theme_name not in THEMES:
        theme_name = DEFAULT_THEME
    if size_name not in TEXT_SIZES:
        size_name = DEFAULT_TEXT_SIZE

    p = THEMES[theme_name]
    _CURRENT = dict(p)
    app.setPalette(build_palette(p))
    app.setStyleSheet(build_style(p, TEXT_SIZES[size_name]))
    try:
        refresh_inspectors()                      # the rail button's icon is drawn in the theme's colours
    except NameError:
        pass


# Kept so the module still exposes a default stylesheet string.
STYLE = build_style(THEMES[DEFAULT_THEME], TEXT_SIZES[DEFAULT_TEXT_SIZE])

# ---------------------------------------------------------------------
# Icons - drawn as SVG (UI-icon idiom: 24-unit grid, 1.75 stroke, round caps
# and joins), rasterised through QSvgRenderer in the colour the button's role
# needs, so they follow the theme. Nothing is read from disk: a frozen
# --onefile exe has no icon folder to find.
# ---------------------------------------------------------------------
_ICON_PATHS = {
    'folder': '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    'files': '<path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z"/><path d="M14 3.5V8h4.5"/><path d="M12 12v5M9.5 14.5h5"/>',
    'undo': '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    'x': '<path d="M6 6l12 12M18 6 6 18"/>',
    'save': '<path d="M5.5 3.5h10l3 3v12a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2z"/><path d="M8 3.5v5h7v-5"/><path d="M8 20.5v-6h8v6"/>',
    'trash': '<path d="M4.5 7h15"/><path d="M9.5 7V4.5h5V7"/><path d="M6.5 7l1 12.5a1.5 1.5 0 0 0 1.5 1.4h6a1.5 1.5 0 0 0 1.5-1.4L17.5 7"/><path d="M10 11v6M14 11v6"/>',
    'copy': '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5v-1a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h1"/>',
    'pencil': '<path d="M4.5 19.5l1-4L16.5 4.5a2.1 2.1 0 0 1 3 3L8.5 18.5z"/><path d="M14.5 6.5l3 3"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'download': '<path d="M12 4v11"/><path d="M7.5 10.5 12 15l4.5-4.5"/><path d="M5 19.5h14"/>',
    'upload': '<path d="M12 15V4"/><path d="M7.5 8.5 12 4l4.5 4.5"/><path d="M5 19.5h14"/>',
    'refresh': '<path d="M18.6 14.4A7 7 0 1 1 16.5 6.6"/><path d="M11.9 6.5L16.5 6.6L15.5 2.2"/>',
    'merge': '<path d="M5 5v3a4 4 0 0 0 4 4h3"/><path d="M19 5v3a4 4 0 0 1-4 4h-3"/><path d="M12 12v7"/><path d="M9.5 16.5 12 19l2.5-2.5"/>',
    'scissors': '<circle cx="6.5" cy="6.5" r="2.5"/><circle cx="6.5" cy="17.5" r="2.5"/><path d="M8.4 8.1 19.5 18.5M8.4 15.9 19.5 5.5"/>',
    'pages': '<rect x="3.5" y="4" width="7" height="9" rx="1"/><rect x="13.5" y="4" width="7" height="9" rx="1"/><path d="M7 17.5h10"/><path d="M14.5 15l2.5 2.5-2.5 2.5"/>',
    'shrink': '<path d="M4.5 9.5h5v-5"/><path d="M9.5 9.5 3.5 3.5"/><path d="M19.5 14.5h-5v5"/><path d="M14.5 14.5l6 6"/>',
    'tag': '<path d="M3.5 12.2V5.5a2 2 0 0 1 2-2h6.7l8.3 8.3a2 2 0 0 1 0 2.8l-6.4 6.4a2 2 0 0 1-2.8 0z"/><circle cx="8.5" cy="8.5" r="1.4"/>',
    'move': '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/><path d="M11 13h6"/><path d="M14.5 10.5 17 13l-2.5 2.5"/>',
    'sliders': '<path d="M4.5 7h9M17.5 7h2M4.5 17h2M10.5 17h9"/><circle cx="15.5" cy="7" r="2"/><circle cx="8.5" cy="17" r="2"/>',
    'help': '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.6a2.5 2.5 0 1 1 3.6 2.2c-.8.4-1.2 1-1.2 1.8"/><path d="M12 16.8h.01"/>',
    'tray': '<path d="M12 15.5V4.5"/><path d="M7.5 9 12 4.5 16.5 9"/><path d="M4.5 15v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3"/>',
}

_ICON_WRAP = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
              'stroke="{c}" stroke-width="1.75" stroke-linecap="round" '
              'stroke-linejoin="round">{body}</svg>')


# Button text (lower-cased, "..." / ellipsis / caret removed) -> glyph.
_BUTTON_ICONS = {
    "choose pdfs to split": "files", "choose pdfs": "files",
    "choose file to merge": "files", "choose folder": "folder",
    "browse": "folder", "open folder": "folder", "open split folder": "folder",
    "open result folder": "folder", "open destination": "folder",
    "open output folder": "folder", "open settings folder": "folder",
    "cancel": "x", "clear": "x",
    "undo last rename": "undo", "undo last move": "undo", "undo my changes": "undo",
    "save this profile": "save", "save as": "save", "save advanced settings": "save",
    "save results": "save", "delete": "trash", "new": "plus",
    "duplicate": "copy", "copy diagnostics": "copy", "rename": "pencil",
    "import": "download", "import presets": "download",
    "export": "upload", "export presets": "upload",
    "run again": "refresh", "reset to defaults": "refresh",
    "move files now": "move",
}
_TAB_ICONS = {
    "PDF Merger": "merge", "PDF Splitter": "scissors", "PDF Optimizer": "shrink",
    "Page Organizer": "pages",
    "Bulk Renamer": "tag", "Bulk File Mover": "move", "Settings": "sliders",
    "Help": "help",
}
_PIXMAP_CACHE = {}


def _svg_pixmap(svg_text: str, px: int, dpr: float = 2.0):
    """Rasterise SVG text to a transparent QPixmap, px logical pixels square."""
    from PySide6.QtGui import QPixmap, QPainter
    from PySide6.QtSvg import QSvgRenderer
    key = (hash(svg_text), px, dpr)
    pm = _PIXMAP_CACHE.get(key)
    if pm is None:
        side = int(round(px * dpr))
        pm = QPixmap(side, side)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        QSvgRenderer(QByteArray(svg_text.encode("utf-8"))).render(painter)
        painter.end()
        pm.setDevicePixelRatio(dpr)
        _PIXMAP_CACHE[key] = pm
    return pm


def glyph_pixmap(key: str, colour: str, px: int = 18):
    body = _ICON_PATHS.get(key)
    if body is None:
        return None
    return _svg_pixmap(_ICON_WRAP.format(c=colour, body=body), px)


def panel_toggle_icon(side: str, open_: bool):
    """Rail button glyph: a rounded window with a side pane. Filled pane = the column is open (click hides it);
    outline only = collapsed (click shows it). Mirrored when the column is on the left."""
    from PySide6.QtGui import QIcon
    icon = QIcon()
    for mode, key in ((QIcon.Normal, "muted"), (QIcon.Active, "accent")):
        c = theme_color(key)
        pane = (f'<path d="M14.5 4.5h4a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2h-4z" fill="{c}" stroke="none" fill-opacity="0.85"/>'
                if open_ else "")
        body = (f'<g{" transform=\"translate(24 0) scale(-1 1)\"" if side == "left" else ""}>'
                f'<rect x="3.5" y="4.5" width="17" height="15" rx="3"/><path d="M14.5 4.5v15"/>{pane}</g>')
        icon.addPixmap(_svg_pixmap(_ICON_WRAP.format(c=c, body=body), 20), mode, QIcon.Off)
    return icon


# The window / taskbar / .exe icon: a vermilion document page with a folded
# corner and the letter D knocked out of it. Original mark; transparent
# corners. AcroD.ico and AcroD.svg are rendered from this same text; keep the
# three in step.
APP_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">'
    '<path d="M64 24H162L204 66V220Q204 232 192 232H64Q52 232 52 220V36Q52 24 64 24Z M100.7 93H123C160.2 93 182.6 120.2 182.6 150C182.6 179.8 160.2 207 123 207H73.4V120.2Z M105.7 122.7H124.3C144.1 122.7 157.8 135.1 157.8 150C157.8 164.9 144.1 177.3 124.3 177.3H105.7Z" fill="#d9482b" fill-rule="evenodd"/>'
    '<path d="M162 24V54Q162 66 174 66H204Z" fill="#8f2412"/>'
    '</svg>'
)


def app_icon():
    """The window / taskbar icon. Fixed colours: it does not follow the theme."""
    from PySide6.QtGui import QIcon
    icon = QIcon()
    for px in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_svg_pixmap(APP_ICON_SVG, px, 1.0))
    return icon


def _icon_key_for(text: str):
    t = (text.replace("\u2026", "").replace("...", "").replace("\u25be", "")
         .strip().lower())
    if t in _BUTTON_ICONS:
        return _BUTTON_ICONS[t]
    if t.startswith("rename "):          # "RENAME 12 FILES"
        return "tag"
    return None


def decorate_icons(root) -> None:
    """Give every recognised push button its glyph in the current theme's
    colours, and the pointing-hand cursor every button should have. Safe to
    call again after a theme change: the glyph key is remembered on the button
    so a button whose text changed ("RENAME 12 FILES") keeps its icon."""
    from PySide6.QtGui import QIcon
    from PySide6.QtCore import QSize
    for b in root.findChildren(QPushButton):
        b.setCursor(Qt.PointingHandCursor)
        key = b.property("iconKey") or _icon_key_for(b.text())
        if not key:
            continue
        b.setProperty("iconKey", key)
        face = theme_color("text" if b.objectName() == "secondary" else "on_accent")
        icon = QIcon()
        icon.addPixmap(glyph_pixmap(key, face), QIcon.Normal)
        icon.addPixmap(glyph_pixmap(key, theme_color("disabled_text")), QIcon.Disabled)
        b.setIcon(icon)
        b.setIconSize(QSize(18, 18))


def tab_icon(key: str):
    """Glyph for a tab: muted normally, accent when selected
    (QIcon.On is what a selected tab asks for)."""
    from PySide6.QtGui import QIcon
    icon = QIcon()
    icon.addPixmap(glyph_pixmap(key, theme_color("muted"), 20), QIcon.Normal, QIcon.Off)
    icon.addPixmap(glyph_pixmap(key, theme_color("accent"), 20), QIcon.Normal, QIcon.On)
    return icon



# ---------------------------------------------------------------------
# Mascot - "Pagey", a chibi sheet of paper with a paperclip, drawn in black
# ink: brush-weight outline, solid ink fold, ink-wash shade, hatched cheeks,
# one light (top-left), one ink colour, a white die-cut border for dark grounds.
# Only the clip and the question bubble follow the theme accent, so Pagey
# reads on every theme: the body is always paper-light and the ink always
# dark. Drawn once here and rendered through QSvgRenderer like the icons.
# Moods: happy (information), curious (question), worried (warning),
# oops (critical). Every id is prefixed per mood (DUP_ID-safe if inlined).
# ---------------------------------------------------------------------
_MASCOT_INK = "#17151f"
_MASCOT_ALARM = "#c62d3a"
_MASCOT_BODY = "M36 18H58L80 40V60Q80 82 58 82H38Q16 82 16 60V40Q16 18 36 18Z"
_MASCOT_MOODS = ("happy", "curious", "worried", "oops")
_KIND_MOOD = {"information": "happy", "question": "curious",
              "warning": "worried", "critical": "oops"}


def _mf(n: float) -> str:
    return ("%.1f" % n).rstrip("0").rstrip(".")


def mascot_svg(mood: str, accent: str = "#5B54B8") -> str:
    """Pagey, drawn in black ink: a variable-weight brush outline (heaviest
    along the bottom), a solid ink fold, an ink-wash shade clipped to the body
    and hatched cheeks. A white die-cut border keeps the ink readable on the
    dark theme. Only the clip and the question bubble follow the theme accent."""
    ink = _MASCOT_INK
    pid = f"pgy-{mood}"
    f = _mf

    def eye(cx, cy, rx=5.0, ry=6.2):
        return (f'<ellipse cx="{f(cx)}" cy="{f(cy)}" rx="{rx}" ry="{ry}" fill="{ink}"/>'
                f'<circle cx="{f(cx - 1.8)}" cy="{f(cy - 2.4)}" r="2.2" fill="#fff"/>'
                f'<circle cx="{f(cx + 2)}" cy="{f(cy + 2.3)}" r="1" fill="#fff"/>')

    def arc_eye(cx, cy):
        return (f'<path d="M{f(cx - 5)} {f(cy + 1.6)}Q{f(cx)} {f(cy - 6)} {f(cx + 5)} {f(cy + 1.6)}" '
                f'fill="none" stroke="{ink}" stroke-width="2.8" stroke-linecap="round"/>')

    def brow(x1, y1, x2, y2):   # tapered brush stroke
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        return (f'<path d="M{f(x1)} {f(y1)}Q{f(mx)} {f(my - 2.2)} {f(x2)} {f(y2)}'
                f'Q{f(mx)} {f(my + 0.9)} {f(x1)} {f(y1)}Z" fill="{ink}"/>')

    def bubble(fill, mark):
        return (f'<circle cx="79" cy="19" r="9" fill="{fill}" stroke="{ink}" stroke-width="2.4"/>'
                + mark)

    cheeks = "".join(
        f'<path d="M{cx + dx - 1} 66l2-4" stroke="{ink}" stroke-width="1.3" '
        f'stroke-linecap="round" opacity=".55"/>' for cx in (27, 69) for dx in (-3, 0, 3))
    arms = (-26, 26) if mood == "happy" else (0, 0)
    if mood == "happy":
        face = (arc_eye(37, 54) + arc_eye(59, 54)
                + f'<path d="M42 61Q48 71 54 61Z" fill="{ink}"/>' + cheeks)
        extra = (f'<path d="M79 11Q79.9 17.1 86 18Q79.9 18.9 79 25Q78.1 18.9 72 18Q78.1 17.1 79 11Z" '
                 f'fill="#fff" stroke="{ink}" stroke-width="1.8" stroke-linejoin="round"/>')
    elif mood == "curious":
        face = (eye(37, 54) + eye(59, 54) + brow(31, 44.5, 42, 42.5) + brow(54, 42, 65, 39.5)
                + f'<ellipse cx="48" cy="64" rx="2.3" ry="2.9" fill="{ink}"/>')
        extra = bubble(accent,
                       '<path d="M75.6 16.4a3.4 3.4 0 1 1 5.2 2.9c-1.2.8-1.8 1.4-1.8 2.6" '
                       'fill="none" stroke="#fff" stroke-width="2.1" stroke-linecap="round"/>'
                       '<circle cx="79" cy="25" r="1.3" fill="#fff"/>')
    elif mood == "worried":
        face = (eye(37, 54) + eye(59, 54) + brow(31, 46, 42, 42.5) + brow(54, 42.5, 65, 46)
                + f'<path d="M41 65q3.5-3.5 7 0t7 0" fill="none" stroke="{ink}" stroke-width="2.3" '
                  'stroke-linecap="round" stroke-linejoin="round"/>')
        extra = ('<path d="M26 31Q31 37.5 26 41.5Q21 37.5 26 31Z" fill="#fff" '
                 f'stroke="{ink}" stroke-width="1.8" stroke-linejoin="round"/>'
                 f'<circle cx="24.6" cy="38" r="1" fill="{ink}"/>')
    else:  # oops
        face = (eye(37, 54, 5.4, 6.6) + eye(59, 54, 5.4, 6.6)
                + brow(31, 46.5, 42, 43) + brow(54, 43, 65, 46.5)
                + f'<path d="M42.5 67Q48 61 53.5 67" fill="none" stroke="{ink}" '
                  'stroke-width="2.4" stroke-linecap="round"/>'
                + '<path d="M32.5 62.5Q35.5 66.5 32.5 69.2Q29.5 66.5 32.5 62.5Z" fill="#fff" '
                  f'stroke="{ink}" stroke-width="1.5" stroke-linejoin="round"/>')
        extra = bubble(_MASCOT_ALARM,
                       '<path d="M79 13.8V20" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/>'
                       '<circle cx="79" cy="24.2" r="1.4" fill="#fff"/>')
    limbs = ('<ellipse cx="38" cy="83.5" rx="8" ry="4.6"/><ellipse cx="58" cy="83.5" rx="8" ry="4.6"/>'
             f'<ellipse cx="15" cy="62" rx="5" ry="7" transform="rotate({arms[0]} 15 62)"/>'
             f'<ellipse cx="81" cy="62" rx="5" ry="7" transform="rotate({arms[1]} 81 62)"/>')
    body = _MASCOT_BODY
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" role="img" '
        f'aria-labelledby="{pid}-t"><title id="{pid}-t">Pagey the paper mascot, {mood}</title>'
        f'<defs><linearGradient id="{pid}-g" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#dcd9e6"/>'
        f'</linearGradient><clipPath id="{pid}-c"><path d="{body}"/></clipPath></defs>'
        f'<g fill="#fff" stroke="#fff" stroke-width="7" stroke-linejoin="round"><path d="{body}"/>{limbs}</g>'
        f'<ellipse cx="48" cy="91" rx="22" ry="2.6" fill="{ink}" opacity=".16"/>'
        f'<g fill="#f4f2fa" stroke="{ink}" stroke-width="2.4">{limbs}</g>'
        f'<path d="{body}" fill="url(#{pid}-g)" stroke="{ink}" stroke-width="2.4" stroke-linejoin="round"/>'
        f'<g clip-path="url(#{pid}-c)"><ellipse cx="72" cy="74" rx="24" ry="22" fill="{ink}" opacity=".13"/>'
        f'<path d="M60 70l6-6M64 75l8-8M56 77l5-5M70 78l6-6" stroke="{ink}" stroke-width="1.2" '
        'stroke-linecap="round" opacity=".5"/></g>'
        f'<path d="M19 64Q22 80 38 82H58Q75 82 79 66" fill="none" stroke="{ink}" stroke-width="3.8" '
        'stroke-linecap="round"/>'
        f'<path d="M58 18L80 40H63Q58 40 58 35Z" fill="{ink}" stroke="{ink}" stroke-width="1.6" '
        'stroke-linejoin="round"/>'
        f'<path d="M24 22V13a4.5 4.5 0 0 1 9 0V30" fill="none" stroke="{ink}" stroke-width="5.2" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<path d="M24 22V13a4.5 4.5 0 0 1 9 0V30" fill="none" stroke="{accent}" stroke-width="2.4" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        + face + extra + '</svg>')


def mascot_pixmap(mood: str = "happy", px: int = 84):
    return _svg_pixmap(mascot_svg(mood, theme_color("accent")), px)


_NATIVE_BOX = {k: getattr(QMessageBox, k)
               for k in ("question", "information", "warning", "critical")}


class Msg:
    """Drop-in for QMessageBox.question / information / warning / critical:
    same arguments, same return value, but Pagey sits in the icon slot with
    a mood that matches the message. Falls back to the native box when the
    Settings tick box is off, and whenever a caller (a test, a script) has
    replaced the QMessageBox static, so patched answers still reach the code."""

    @staticmethod
    def _show(kind, parent, title, text, buttons=None, default=None):
        native = getattr(QMessageBox, kind)
        if native != _NATIVE_BOX[kind] or not setting("mascot_dialogs"):
            extra = [] if buttons is None else (
                [buttons] if default is None else [buttons, default])
            return native(parent, title, text, *extra)
        box = QMessageBox(parent)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIconPixmap(mascot_pixmap(_KIND_MOOD[kind]))
        if buttons is None:
            buttons = (QMessageBox.Yes | QMessageBox.No) if kind == "question" \
                else QMessageBox.Ok
        box.setStandardButtons(buttons)
        if default is not None:
            box.setDefaultButton(default)
        if kind == "question" and (buttons & QMessageBox.No) \
                and not (buttons & QMessageBox.Cancel):
            box.setEscapeButton(QMessageBox.No)
        # QMessageBox pins its icon to the top-left cell; centre Pagey on the
        # text block instead. The layout is (re)built when the box is shown,
        # so the alignment is applied right after that.
        QTimer.singleShot(0, lambda b=box: Msg._centre_icon(b))
        return QMessageBox.StandardButton(box.exec())

    @staticmethod
    def _centre_icon(box):
        try:
            icon = box.findChild(QLabel, "qt_msgboxex_icon_label")
            lay = box.layout()
            if icon is not None and lay is not None:
                lay.setAlignment(icon, Qt.AlignCenter)
                lay.invalidate()
        except RuntimeError:        # box already closed
            pass

    @staticmethod
    def question(parent, title, text, buttons=None, default=None):
        return Msg._show("question", parent, title, text, buttons, default)

    @staticmethod
    def information(parent, title, text, buttons=None, default=None):
        return Msg._show("information", parent, title, text, buttons, default)

    @staticmethod
    def warning(parent, title, text, buttons=None, default=None):
        return Msg._show("warning", parent, title, text, buttons, default)

    @staticmethod
    def critical(parent, title, text, buttons=None, default=None):
        return Msg._show("critical", parent, title, text, buttons, default)


def _ask_signed_choice(parent, opts) -> str:
    """Ask what to do with text going into a digitally signed PDF: 'keep' (add it after the signature), 'remove'
    (rebuild the file and drop the signature) or 'cancel'. A module-level function so a test can answer it."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("This PDF is digitally signed")
    lines = ["Adding text to a signed document should only be done for an approved item."]
    keep = None
    if opts.get("can_keep"):
        lines.append("Keep the signature: the text is added as changes after signing. The signature stays valid, and "
                     "PDF viewers will show that the file was changed after it was signed.")
        lines.append("Remove the signature: the file is rebuilt and the signature is gone.")
    else:
        lines.append(opts.get("why_not") or "")
        lines.append("Saving this will remove the signature from the new copy. The original is not touched.")
    box.setText("\n\n".join(l for l in lines if l))
    if opts.get("can_keep"):
        keep = box.addButton("Keep the signature", QMessageBox.AcceptRole)
    remove = box.addButton("Remove the signature and save", QMessageBox.DestructiveRole)
    cancel = box.addButton("Cancel", QMessageBox.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    clicked = box.clickedButton()
    if keep is not None and clicked is keep:
        return "keep"
    if clicked is remove:
        return "remove"
    return "cancel"


# =====================================================================
# ==== acrod_core BEGIN ================================================
# Everything from here to "acrod_core END" is framework-neutral: PDF ops,
# the Optimizer engine, the rename engine and undo, profiles,
# presets, settings. No Qt name may be referenced inside it - a test
# (tests/test_p4_core.py) parses this region and fails the build if one
# is. That is what keeps a future `acrod_core.py` split a cut-and-paste
# and what lets `--cli` below run the same code the tabs run.
# =====================================================================
# pdf_ops — pure functions, no Qt. Headlessly testable.
# =====================================================================

class PdfOpError(Exception):
    """Operator-facing failure with a plain-language message."""


def _pypdf():
    """Import pypdf on first use.

    MEASURED: importing pypdf at module scope cost 400ms of a 5.9s cold start
    (6.8%). Nothing on the Renamer or Mover tabs needs it, and most launches
    never touch a PDF at all, so the cost is deferred to the first PDF action.
    """
    from pypdf import PdfReader, PdfWriter
    return PdfReader, PdfWriter


PDF_FILE_MAX = 1024 * 1024 * 1024          # larger than this is refused unread: the libraries load a file into memory


def pdf_file_problem(path: str):
    """None when `path` can be a PDF at all; otherwise the plain-language reason it is refused.
    Looks at the size and the first 1 KB only, so a 2 GB file of zeros costs nothing. Every PDF
    reader (pypdf, pdfium, Acrobat) wants '%PDF-' within the first 1024 bytes."""
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
        if size > PDF_FILE_MAX:
            return (f"'{name}' is {size / (1 << 30):.1f} GB, which is too large to open safely. "
                    f"Acro-Data opens PDFs up to {PDF_FILE_MAX >> 30} GB.")
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        return None                            # unreadable or locked: the caller's own error says so
    if size and b"%PDF-" not in head:
        return f"'{name}' is not a PDF (it does not start like one), so it was not opened."
    return None


def _open_reader(path: str) -> "PdfReader":
    """Open a PDF, converting library errors into plain-language ones."""
    PdfReader, _ = _pypdf()
    name = os.path.basename(path)
    if not os.path.isfile(path):
        raise PdfOpError(f"'{name}' is not there any more, or is not a file.")
    problem = pdf_file_problem(path)
    if problem:
        raise PdfOpError(problem)
    try:
        reader = PdfReader(path)
    except OSError as e:
        # A locked or unreachable file is not a broken PDF, and saying so
        # sends the operator to close Acrobat instead of hunting a corruption.
        raise PdfOpError(describe_lock_failure(path, e)) from e
    except Exception as e:
        raise PdfOpError(
            f"'{name}' could not be read. It may be damaged or only partly "
            f"copied. (Details: {e})"
        ) from e
    if reader.is_encrypted:
        # Acrobat's "secured" PDFs carry an OWNER password only - they open
        # fine with an empty user password, and many real-world PDFs are like this.
        # Refusing them outright reported the wrong cause.
        try:
            opened = reader.decrypt("")
        except Exception:
            opened = 0
        if not opened:
            raise PdfOpError(
                f"'{name}' is password-protected. "
                "Remove the password before processing it."
            )
    return reader


def _atomic_write(writer: "PdfWriter", dest_path: str) -> None:
    """Write to a sibling temp file, then swap into place.

    A crash or full disk mid-write leaves the temp file behind and the
    destination untouched, instead of a truncated PDF.
    """
    # A fixed "<dest>.tmp" name collides when two operations target the same
    # file, and os.replace() without fsync can leave a truncated file at the
    # real destination if an SMB session drops right after the rename.
    tmp_path = f"{dest_path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp_path, "wb") as f:
            writer.write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dest_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


LAST_SUFFIX_FMT = "_Last{n}"


def split_pdf(src_path: str, split_after: int, out_dir_name: str = None,
              cover_suffix: str = None, remaining_suffix: str = None,
              from_end: bool = False):
    """Split src_path into two parts.

    Default: the first `split_after` pages become {stem}_Cover.pdf and the rest
    {stem}_Remaining.pdf. With from_end=True the LAST `split_after` pages
    become {stem}_Last<N>.pdf and everything before them {stem}_Remaining.pdf.
    Output goes into a subfolder named out_dir_name, created alongside the
    source file. The source is left intact.

    Returns (extracted_path, remaining_path): cover / last-N first.
    """
    if split_after < 1:
        raise PdfOpError("Split point must be at least 1 page.")

    reader = _open_reader(src_path)
    total = len(reader.pages)
    if total <= split_after:
        what = (f"splitting off the last {plural(split_after, 'page')}"
                if from_end else f"splitting after page {split_after}")
        raise PdfOpError(
            f"'{os.path.basename(src_path)}' has {plural(total, 'page')}; "
            f"{what} would leave nothing behind."
        )
    boundary = total - split_after if from_end else split_after

    src_dir = os.path.dirname(os.path.abspath(src_path))
    out_dir_name = out_dir_name or setting("split_dir_name")
    cover_suffix = cover_suffix or setting("cover_suffix")
    remaining_suffix = remaining_suffix or setting("remaining_suffix")
    out_dir = os.path.join(src_dir, out_dir_name)
    os.makedirs(out_dir, exist_ok=True)

    stem = os.path.splitext(os.path.basename(src_path))[0]
    # Re-splitting used to overwrite the previous halves in place, including
    # ones that had since been signed or edited. Verified: a second run
    # replaced a 2-page cover with a 1-page one and reported plain success.
    first_suffix = (LAST_SUFFIX_FMT.format(n=split_after) if from_end
                    else cover_suffix)
    cover_path = _unique_destination(os.path.join(out_dir, f"{stem}{first_suffix}.pdf"))
    remaining_path = _unique_destination(
        os.path.join(out_dir, f"{stem}{remaining_suffix}.pdf"))

    _, PdfWriter = _pypdf()
    staged, published = [], []
    try:
        # from_end: the extracted piece is the TAIL, so the two page ranges
        # swap destinations; the return order (extracted, remaining) holds.
        ranges = ((boundary, total, cover_path), (0, boundary, remaining_path)) \
            if from_end else ((0, boundary, cover_path), (boundary, total, remaining_path))
        for start, stop, dest in ranges:
            writer = PdfWriter()
            # append() carries annotations and named destinations that a bare
            # add_page() loop drops. Outlines are excluded: a parent
            # document's bookmarks point at pages not in this fragment.
            writer.append(reader, pages=(start, stop), import_outline=False)
            if reader.metadata is not None:
                writer.add_metadata(reader.metadata)
            # Both halves are staged first and only published together. A
            # failure on the second half used to leave the first on disk
            # while the file was reported as Failed - a half-split result
            # that looks complete in Explorer.
            tmp = f"{dest}.{uuid.uuid4().hex[:8]}.part"
            with open(tmp, "wb") as fh:
                writer.write(fh)
                fh.flush()
                os.fsync(fh.fileno())
            staged.append((tmp, dest))
        published = []
        for tmp, dest in staged:
            os.replace(tmp, dest)
            published.append(dest)
    except Exception:
        # Roll back anything already published. Safe because both
        # destinations are guaranteed-new names from _unique_destination,
        # so nothing that existed beforehand can be removed here.
        for dest in published:
            try:
                if os.path.exists(dest):
                    os.remove(dest)
            except OSError:
                pass
        for tmp, _dest in staged:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
        raise

    return cover_path, remaining_path


# ---------------------------------------------------------------------
# Page Organizer engine: pure functions, no Qt. The tab only builds a
# "page plan" and hands it here; everything that touches a PDF is below.
# ---------------------------------------------------------------------
_VALID_ROTATIONS = (0, 90, 180, 270)


def source_stamp(path: str):
    """(mtime_ns, size) - what a file looked like when it was opened, so a
    save can refuse if somebody changed it while the operator was looking."""
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


def pdf_page_count(path: str) -> int:
    return len(_open_reader(path).pages)


def validate_page_plan(plan, total: int):
    """plan = [(source_page_index, rotation_degrees), ...] in OUTPUT order.
    Returns a clean list of (int, int). Raises PdfOpError with a plain
    message on anything that could write the wrong pages."""
    if not isinstance(plan, (list, tuple)) or not plan:
        raise PdfOpError("Nothing to save: every page has been removed.")
    clean, seen = [], set()
    for item in plan:
        try:
            idx, rot = item
        except (TypeError, ValueError):
            raise PdfOpError("The page list is damaged; nothing was written.")
        if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < total:
            raise PdfOpError(f"Page {idx!r} is not in this {plural(total, 'page')} file.")
        if idx in seen:
            raise PdfOpError(f"Page {idx + 1} appears twice; nothing was written.")
        if isinstance(rot, bool) or rot not in _VALID_ROTATIONS:
            raise PdfOpError(f"Rotation {rot!r} is not a quarter turn.")
        seen.add(idx)
        clean.append((idx, rot))
    return clean


MULTI_SOURCES_MAX = 32                      # distinct PDFs one saved file may be built from


def is_multi_plan(plan) -> bool:
    """True when the plan names its source file per page: items are (source, page, rotation)."""
    return (isinstance(plan, (list, tuple)) and bool(plan)
            and any(isinstance(i, (list, tuple)) and len(i) == 3 for i in plan))


def validate_multi_plan(plan, totals):
    """plan = [(source_number, page_index, rotation), ...] in OUTPUT order; source 0 is the file
    being edited, 1.. are the others; totals[n] is source n's page count. A page may appear more
    than once (a copy). Returns a clean list of (int, int, int); raises PdfOpError on anything
    that could write the wrong pages."""
    if not isinstance(plan, (list, tuple)) or not plan:
        raise PdfOpError("Nothing to save: every page has been removed.")
    clean = []
    for item in plan:
        try:
            sid, idx, rot = item
        except (TypeError, ValueError):
            raise PdfOpError("The page list is damaged; nothing was written.")
        if isinstance(sid, bool) or not isinstance(sid, int) or not 0 <= sid < len(totals):
            raise PdfOpError("The page list names a file that is not part of this save; nothing was written.")
        if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < totals[sid]:
            raise PdfOpError(f"Page {idx!r} is not in this {plural(totals[sid], 'page')} file.")
        if isinstance(rot, bool) or rot not in _VALID_ROTATIONS:
            raise PdfOpError(f"Rotation {rot!r} is not a quarter turn.")
        clean.append((sid, idx, rot))
    return clean


def multi_plan_is_unchanged(plan, total: int) -> bool:
    return (len(plan) == total and all(sid == 0 and idx == k and rot == 0
                                       for k, (sid, idx, rot) in enumerate(plan)))


def plan_is_unchanged(plan, total: int) -> bool:
    return [i for i, _r in plan] == list(range(total)) and all(r == 0 for _i, r in plan)


# ---------------------------------------------------------------------
# Floating text for the Page Organizer's reader.
#
# A text box is plain data: its place and width are FRACTIONS of the page as
# the reader shows it before any Organizer rotation, so the same box fits
# whatever zoom or file it lands in. Nothing here touches Qt widgets or
# pikepdf until a box is actually drawn or saved (a launch that never adds
# text never pays for any of it). Boxes are drawn by Qt (real embedded fonts,
# real wrapping) onto a one-page-per-box-page overlay PDF and laid over the
# saved page with pikepdf, so the words are searchable and nothing in the
# original page is rewritten.
# ---------------------------------------------------------------------
TEXT_FONT_DEFAULT = "Arial"
TEXTBOX_SIZES = (8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72)
TEXT_ALIGNS = ("left", "center", "right")
TEXT_BOX_MAX_CHARS = 20000
TEXT_BOXES_PER_PAGE_MAX = 200
TEXT_MIN_W = 0.03
TEXT_ERASE_MAX = 80                         # rectangles one box may cover
_TEXT_UNITS = 10.0                          # drawing units per point: 1 unit = 0.1 pt, so sizes keep a decimal


@dataclass(frozen=True)
class TextBox:
    x: float = 0.1
    y: float = 0.1
    w: float = 0.4
    h: float = 0.03
    text: str = ""
    family: str = TEXT_FONT_DEFAULT
    size: float = 12.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: str = "#000000"
    align: str = "left"
    spacing: float = 1.0
    # Editing text that is already on the page: `erase` are the rectangles (x0, y0, x1, y1 as fractions of the page as
    # displayed) the old words occupied, `cover` the colour that hides them where they cannot be taken out of the page,
    # `orig` what they said (a box whose text is unchanged is not an edit).
    erase: tuple = ()
    cover: str = "#ffffff"
    orig: str = ""


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    return min(max(v, lo), hi)


def clean_text_box(box) -> "TextBox":
    """A TextBox whose every field is sane, whatever it was built from (a dict from a
    test or a half-edited box). Never raises: a field that cannot be used takes its default."""
    d = asdict(box) if isinstance(box, TextBox) else dict(box or {})
    base = TextBox()
    w = _clamp(d.get("w"), TEXT_MIN_W, 1.0, base.w)
    x = _clamp(d.get("x"), 0.0, 1.0 - w, base.x)
    h = _clamp(d.get("h"), 0.005, 1.0, base.h)
    y = _clamp(d.get("y"), 0.0, 1.0 - min(h, 1.0), base.y)
    color = str(d.get("color") or "")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = base.color
    align = d.get("align") if d.get("align") in TEXT_ALIGNS else "left"
    family = str(d.get("family") or TEXT_FONT_DEFAULT)[:80]
    text = str(d.get("text") or "")[:TEXT_BOX_MAX_CHARS]
    cover = str(d.get("cover") or "")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", cover):
        cover = base.cover
    erase = []
    try:
        for r in list(d.get("erase") or ())[:TEXT_ERASE_MAX]:
            a, b, c, e = (_clamp(v, 0.0, 1.0, None) for v in r)
            if None in (a, b, c, e) or c - a < 0.001 or e - b < 0.001:
                continue
            erase.append((a, b, c, e))
    except (TypeError, ValueError):
        erase = []
    return TextBox(x=x, y=y, w=w, h=h, text=text, family=family,
                   size=_clamp(d.get("size"), 4.0, 200.0, base.size),
                   bold=bool(d.get("bold")), italic=bool(d.get("italic")),
                   underline=bool(d.get("underline")), color=color.lower(), align=align,
                   spacing=_clamp(d.get("spacing"), 0.8, 3.0, 1.0),
                   erase=tuple(erase), cover=cover.lower(), orig=str(d.get("orig") or "")[:TEXT_BOX_MAX_CHARS])


def text_rot_transform(rot: int, base_w: float, base_h: float):
    """QTransform (in drawing units) taking a point of the page BEFORE an Organizer rotation to the
    same point on the page after it. `base_w/base_h` are the unrotated page's sides in drawing units."""
    t = QTransform()
    rot = int(rot) % 360
    if rot == 90:
        t.translate(base_h, 0)
        t.rotate(90)
    elif rot == 180:
        t.translate(base_w, base_h)
        t.rotate(180)
    elif rot == 270:
        t.translate(0, base_w)
        t.rotate(270)
    return t


def _text_document(box, page_w_pt: float):
    """The laid-out text of one box, in drawing units (0.1 pt). The same document is painted on
    screen and into the overlay PDF, so the page you see is the page that is saved."""
    from PySide6.QtGui import (QTextDocument, QFont, QTextOption, QTextCursor,
                               QTextBlockFormat, QTextCharFormat)
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    font = QFont(box.family)
    font.setPixelSize(max(1, int(round(box.size * _TEXT_UNITS))))
    font.setBold(box.bold)
    font.setItalic(box.italic)
    font.setUnderline(box.underline)
    font.setHintingPreference(QFont.PreferNoHinting)   # advances scale linearly: the same wraps at every size and on every OS
    doc.setDefaultFont(font)
    opt = QTextOption()
    opt.setAlignment({"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}[box.align])
    opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    doc.setDefaultTextOption(opt)
    doc.setTextWidth(max(1.0, box.w * page_w_pt * _TEXT_UNITS))
    doc.setPlainText(box.text)
    cur = QTextCursor(doc)
    cur.select(QTextCursor.Document)
    cf = QTextCharFormat()
    cf.setForeground(QColor(box.color))
    cur.mergeCharFormat(cf)
    bf = QTextBlockFormat()
    bf.setLineHeight(box.spacing * 100.0, 1)           # 1 = ProportionalHeight
    cur.mergeBlockFormat(bf)
    return doc


def text_box_height_frac(box, page_w_pt: float, page_h_pt: float) -> float:
    """How tall the box's text is, as a fraction of the page height (at least one line)."""
    doc = _text_document(replace(box, text=box.text or " "), page_w_pt)
    units = doc.size().height()
    if box.spacing < 1.0:
        # Lines packed closer than their natural height: the last line's descenders hang below the
        # document's own height. Keep them inside the box.
        from PySide6.QtGui import QFontMetricsF
        units += (1.0 - box.spacing) * QFontMetricsF(doc.defaultFont()).height()
    return max(0.005, units / _TEXT_UNITS / max(1.0, page_h_pt))


def draw_text_cover(painter, box, page_w_pt: float, page_h_pt: float):
    """Paint the patches that hide the old words an edited box replaces (units: 0.1 pt, as draw_text_box)."""
    if not box.erase:
        return
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(box.cover))
    for (a, b, c, e) in box.erase:
        painter.drawRect(QRectF(a * page_w_pt * _TEXT_UNITS, b * page_h_pt * _TEXT_UNITS,
                                (c - a) * page_w_pt * _TEXT_UNITS, (e - b) * page_h_pt * _TEXT_UNITS))
    painter.restore()


def draw_text_box(painter, box, page_w_pt: float, page_h_pt: float):
    """Paint one box. The painter's units must already be 0.1 pt per unit."""
    draw_text_cover(painter, box, page_w_pt, page_h_pt)
    if not box.text.strip():
        return
    doc = _text_document(box, page_w_pt)
    painter.save()
    painter.translate(box.x * page_w_pt * _TEXT_UNITS, box.y * page_h_pt * _TEXT_UNITS)
    doc.drawContents(painter)
    painter.restore()


def text_overlay_pdf(pages, dest_path: str):
    """Write one overlay page per entry of `pages` = [(shown_w_pt, shown_h_pt, rot, boxes)] to dest_path.
    shown_* is the page AFTER its Organizer rotation `rot`; the boxes are placed as they were on the
    unrotated page and turned with it."""
    from PySide6.QtGui import QPdfWriter, QPageSize
    from PySide6.QtCore import QSizeF, QMarginsF
    writer = QPdfWriter(dest_path)
    writer.setResolution(int(72 * _TEXT_UNITS))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    painter = None
    for n, (sw, sh, rot, boxes) in enumerate(pages):
        writer.setPageSize(QPageSize(QSizeF(float(sw), float(sh)), QPageSize.Point))
        if painter is None:
            painter = QPainter(writer)
            if not painter.isActive():
                raise PdfOpError("The text could not be written next to the saved file. Check that the folder is writable.")
        else:
            writer.newPage()
        bw, bh = (sh, sw) if int(rot) % 180 else (sw, sh)
        painter.save()
        painter.setTransform(text_rot_transform(rot, bw * _TEXT_UNITS, bh * _TEXT_UNITS))
        for box in boxes:
            draw_text_box(painter, box, bw, bh)
        painter.restore()
    if painter is not None:
        painter.end()


TEXT_PAGE_MAX_PT = 14400.0                 # the PDF limit (200 in); the reader clamps to the same
TEXT_BOXES_TOTAL_MAX = 5000                # boxes in one save
TEXT_CHARS_TOTAL_MAX = 2_000_000           # characters in one save


def _visible_box(page, number=0):
    """(x0, y0, x1, y1) of the part of a page that is shown, in its own units (CropBox within MediaBox).
    A page whose size cannot be trusted (not finite, empty, or past the PDF limit) raises: text laid on a
    guessed page would be missing or misplaced, and a save must not report success for that."""
    def _box(key):
        try:
            b = page.obj.get(key)
            v = [float(x) for x in b] if b is not None else None
            return v if v is not None and len(v) == 4 else None
        except Exception:
            return None
    m = _box("/MediaBox") or [0.0, 0.0, 612.0, 792.0]
    c = _box("/CropBox") or m
    x0, y0 = max(min(c[0], c[2]), min(m[0], m[2])), max(min(c[1], c[3]), min(m[1], m[3]))
    x1, y1 = min(max(c[0], c[2]), max(m[0], m[2])), min(max(c[1], c[3]), max(m[1], m[3]))
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 - x0 < 1 or y1 - y0 < 1:
        x0, y0, x1, y1 = min(m[0], m[2]), min(m[1], m[3]), max(m[0], m[2]), max(m[1], m[3])
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or not (
            1 <= x1 - x0 <= TEXT_PAGE_MAX_PT and 1 <= y1 - y0 <= TEXT_PAGE_MAX_PT):
        raise PdfOpError(f"Page {number} has a page size text cannot be placed on, so nothing was saved. "
                         "Take the text off that page, or save without it.")
    return x0, y0, x1, y1


def apply_text_overlays(path: str, per_page, audit: str = "", report=None):
    """Lay each page's text boxes over the finished PDF at `path`. `per_page` = [(organizer_rotation,
    boxes)] in the file's page order. A page with no boxes is not touched. Returns the number of boxes drawn.
    `report`, when given, is filled with how the old words of edited text were dealt with: "text_removed" (taken out of the
    page) and "text_covered" (hidden under a rectangle, still inside the file) as counts of rectangles."""
    wanted = [k for k, (_r, boxes) in enumerate(per_page) if boxes]
    if not wanted:
        return 0
    if report is None:
        report = {}
    report.setdefault("text_removed", 0)
    report.setdefault("text_covered", 0)
    pk = _pikepdf()
    over = path + ".over"
    tmp = path + ".text"
    try:
        with pk.open(path) as pdf:
            if len(pdf.pages) != len(per_page):
                raise PdfOpError("The text could not be placed: the page count changed while saving.")
            pages, rects = [], []
            for k in wanted:
                page = pdf.pages[k]
                x0, y0, x1, y1 = _visible_box(page, k + 1)
                try:
                    turn = int(page.obj.get("/Rotate", 0)) % 360
                except Exception:
                    turn = 0
                if turn % 90:
                    turn = 0                                  # viewers show an odd /Rotate unturned
                w, h = (y1 - y0, x1 - x0) if turn % 180 else (x1 - x0, y1 - y0)
                pages.append((w, h, per_page[k][0], per_page[k][1]))
                rects.append(pk.Rectangle(x0, y0, x1, y1))
            erase_at = [k for k in wanted if any(b.erase for b in per_page[k][1])]
            if erase_at:
                gone = _take_out_old_text(pdf, path, {k: per_page[k] for k in erase_at})
                for n, k in enumerate(wanted):
                    if k not in gone:
                        continue
                    kept = []
                    for j, b in enumerate(per_page[k][1]):
                        flags = gone[k][j]
                        report["text_removed"] += sum(1 for f in flags if f)
                        report["text_covered"] += sum(1 for f in flags if not f)
                        kept.append(replace(b, erase=tuple(r for r, f in zip(b.erase, flags) if not f)))
                    pages[n] = (pages[n][0], pages[n][1], pages[n][2], kept)
            text_overlay_pdf(pages, over)
            with pk.open(over) as ov:
                for j, k in enumerate(wanted):
                    pg = pdf.pages[k]
                    # What Page.add_overlay does, step by step, minus its contents_coalesce(): that call decodes
                    # and merges every content stream of the page, so one stream that inflates to gigabytes
                    # (a decompression bomb) would be inflated here too. Separate streams stay untouched.
                    formx = pk.Page(ov.pages[j]).as_form_xobject()
                    name = pg.add_resource(formx, pk.Name.XObject)
                    place = pg.calc_form_xobject_placement(formx, name, rects[j], allow_shrink=True, allow_expand=True)
                    pg.contents_add(pdf.make_stream(b"q\n"), prepend=True)       # fence the page's own drawing off
                    pg.contents_add(pdf.make_stream(b"\nQ\n"), prepend=False)
                    pg.contents_add(pdf.make_stream(place), prepend=False)
                if audit:
                    pdf.docinfo[pk.Name("/AcroDEdit")] = audit
                pdf.save(tmp)
            with pk.open(tmp) as chk:
                if len(chk.pages) != len(per_page):
                    raise PdfOpError("The text could not be placed: the finished file has the wrong number of pages.")
        os.replace(tmp, path)
    finally:
        for f in (over, tmp):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except OSError:
                pass
    return sum(len(per_page[k][1]) for k in wanted)


def _take_out_old_text(pdf, path, edits):
    """For each page in `edits` ({k: (organizer_rotation, boxes)}) try to take the old words of every box with `erase`
    rectangles out of page k of the open pikepdf file `pdf` (saved at `path`). Returns {k: [[removed? per rectangle] per box]};
    a rectangle that is not removed is covered by the caller. A page whose rewrite does not check out stays as it was."""
    pk = _pikepdf()
    import tempfile
    out = {k: [[False] * len(b.erase) for b in boxes] for k, (_r, boxes) in edits.items()}
    pdfium = _pdfium()
    tmp_dir = tempfile.mkdtemp(prefix="acrod_rm_", dir=os.path.dirname(os.path.abspath(path)))
    try:
        with _PDFIUM_LOCK:
            try:
                doc = pdfium_open_for_render(pdfium, path)
            except Exception:
                return out
        try:
            for k, (rot, boxes) in edits.items():
                flat = [(j, r, rotate_frac_rect(rc, rot)) for j, b in enumerate(boxes) for r, rc in enumerate(b.erase)]
                try:
                    cont = pdf.pages[k].obj.get("/Contents")
                    streams = list(cont) if isinstance(cont, pk.Array) else [cont]
                    if sum(int(c.get("/Length", 0)) for c in streams if c is not None) > TEXT_REMOVE_CONTENT_MAX:
                        continue                                # too big to rewrite and check: its old words are covered
                except Exception:
                    continue
                try:
                    with _PDFIUM_LOCK:
                        content, flags = _remove_text_on_page(doc, k, [f[2] for f in flat], tmp_dir)
                except Exception:
                    continue                                    # whatever went wrong, the page stays as it was and is covered
                if content is None:
                    continue
                try:
                    with pk.open(content) as src:
                        sp = src.pages[0]
                        dst = pdf.pages[k]
                        def _carry(obj):                       # copy_foreign takes an indirect object only
                            return pdf.copy_foreign(obj if obj.is_indirect else src.make_indirect(obj))
                        res = sp.obj.get("/Resources")
                        newc = _carry(sp.obj["/Contents"]) if "/Contents" in sp.obj else pdf.make_stream(b"")
                        dst.obj["/Contents"] = newc
                        dst.obj["/Resources"] = _carry(res) if res is not None else pk.Dictionary()
                except Exception:
                    continue
                for (j, r, _rc), f in zip(flat, flags):
                    out[k][j][r] = bool(f)
        finally:
            with _PDFIUM_LOCK:
                try:
                    doc.close()
                except Exception:
                    pass
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return out


# ---- text that is already on a page -------------------------------------------------------------------
# pdfium knows, per character, where it sits, how big it is, its font and its colour. `reader_text_layout` turns that
# into "segments" (one run of text on one line) in the same fractions-of-the-displayed-page the text boxes use, so a
# click can pick a line and the line can become an editable box that sits exactly on the old words.
TEXT_LAYOUT_MAX_CHARS = 20000                # a page with more characters than this is not laid out (a drawing, a bomb)
TEXT_LAYOUT_MAX_SEGS = 3000


def text_family_for_font(name: str, flags: int = 0):
    """(family, bold, italic) for a PDF font name and pdfium's descriptor flags. The page's own font may not be on this
    machine (a subset of it never is), so the nearest ordinary family is chosen: the new words look like the old ones
    without claiming to be the same font."""
    n = (name or "").split("+", 1)[-1]
    low = n.lower()
    bold = any(k in low for k in ("bold", "black", "heavy", "semibold", "demi")) or low.endswith(("-bd", ",bd"))
    italic = any(k in low for k in ("italic", "oblique")) or bool(flags & 64) or low.endswith(("-it", ",it"))
    serif_name = any(k in low for k in ("times", "serif", "georgia", "garamond", "palatino", "cambria", "minion",
                                        "century")) and "sans" not in low
    sans_name = any(k in low for k in ("arial", "helvetica", "calibri", "verdana", "tahoma", "segoe"))
    if "courier" in low or "consolas" in low or ("mono" in low and "monotype" not in low) or (flags & 1):
        fam = "Courier New"
    elif serif_name or ((flags & 2) and not sans_name):
        fam = "Times New Roman"
    else:
        fam = "Arial"
    return fam, bold, italic


def reader_text_layout(doc, index, limit=TEXT_LAYOUT_MAX_SEGS):
    """Runs of text on one page, as dicts with fractions of the page as DISPLAYED (its own /Rotate applied, no Organizer
    rotation): x0 y0 x1 y1 (the glyphs' tight box), by (baseline), size (pt), text, family, bold, italic, color, edit
    (False for text that is not horizontal on screen, which a box cannot reproduce). The caller holds _PDFIUM_LOCK."""
    import ctypes
    import math as _m
    pdfium = _pdfium()
    raw = pdfium.raw
    page = doc[index]
    tp = None
    try:
        pw, ph = page.get_size()
        pw, ph = float(pw), float(ph)
        if not (_m.isfinite(pw) and _m.isfinite(ph)) or pw < 1 or ph < 1:
            return []
        turn = int(page.get_rotation()) % 360
        tp = page.get_textpage()
        n = int(tp.count_chars())
        if n <= 0 or n > TEXT_LAYOUT_MAX_CHARS:
            return []
        W, H = int(pw * 1000), int(ph * 1000)
        dx, dy = ctypes.c_int(), ctypes.c_int()

        def dev(px, py):
            raw.FPDF_PageToDevice(page.raw, 0, 0, W, H, 0, px, py, dx, dy)
            return dx.value / 1000.0, dy.value / 1000.0
        ox, oy = ctypes.c_double(), ctypes.c_double()
        mat = raw.FS_MATRIX()
        buf = ctypes.create_string_buffer(128)
        flg = ctypes.c_int()
        cr, cg, cb, ca = (ctypes.c_uint() for _ in range(4))
        segs, cur = [], None

        def flush():
            nonlocal cur
            if cur is not None and cur["text"].strip():
                segs.append(cur)
            cur = None
        for i in range(n):
            if len(segs) >= limit:
                break
            if raw.FPDFText_IsGenerated(tp.raw, i):
                continue
            code = raw.FPDFText_GetUnicode(tp.raw, i)
            if code in (10, 13, 0xFFFE, 0xFFFF) or code == 0:
                flush()
                continue
            ch = chr(code) if code < 0x110000 else "?"
            try:
                l, b, r, t = tp.get_charbox(i, loose=False)
            except Exception:
                continue
            x0, y0 = dev(l, t)
            x1, y1 = dev(r, b)
            if x1 < x0:
                x0, x1 = x1, x0
            if y1 < y0:
                y0, y1 = y1, y0
            raw.FPDFText_GetCharOrigin(tp.raw, i, ctypes.byref(ox), ctypes.byref(oy))
            bx, by = dev(ox.value, oy.value)
            size = float(raw.FPDFText_GetFontSize(tp.raw, i))
            if raw.FPDFText_GetMatrix(tp.raw, i, ctypes.byref(mat)):
                size *= _m.hypot(mat.a, mat.b) or 1.0
            if not _m.isfinite(size) or size < 1 or size > 400:
                flush()
                continue
            ang = _m.degrees(raw.FPDFText_GetCharAngle(tp.raw, i)) - turn
            horiz = abs(((ang + 180.0) % 360.0) - 180.0) < 6.0
            nm = raw.FPDFText_GetFontInfo(tp.raw, i, buf, 128, ctypes.byref(flg))
            fname = buf.value.decode("latin-1", "replace") if nm else ""
            raw.FPDFText_GetFillColor(tp.raw, i, ctypes.byref(cr), ctypes.byref(cg), ctypes.byref(cb), ctypes.byref(ca))
            color = "#%02x%02x%02x" % (min(255, cr.value), min(255, cg.value), min(255, cb.value))
            if cur is not None:
                gap = x0 - cur["_x1"]
                if (not horiz) != (not cur["edit"]) or abs(by - cur["_by"]) > 0.4 * cur["_size"] \
                        or gap > 1.6 * cur["_size"] or gap < -0.8 * cur["_size"]:
                    flush()
            if cur is None:
                cur = {"text": "", "_x0": x0, "_y0": y0, "_x1": x1, "_y1": y1, "_by": by, "_size": size,
                       "edit": bool(horiz), "_styles": [], "_sizes": [], "_ch": 0}
            if code != 0x20 or cur["text"]:
                cur["text"] += ch
            cur["_x0"], cur["_y0"] = min(cur["_x0"], x0), min(cur["_y0"], y0)
            cur["_x1"], cur["_y1"] = max(cur["_x1"], x1), max(cur["_y1"], y1)
            cur["_size"] = size
            cur["_sizes"].append(size)
            cur["_styles"].append((fname, int(flg.value), color))
        flush()
        out = []
        for s in segs:
            sizes = sorted(s["_sizes"])
            size = sizes[len(sizes) // 2]
            styles = {}
            for st in s["_styles"]:
                styles[st] = styles.get(st, 0) + 1
            fname, flags, color = max(styles, key=styles.get)
            fam, bold, italic = text_family_for_font(fname, flags)
            out.append({"x0": max(0.0, s["_x0"] / pw), "y0": max(0.0, s["_y0"] / ph),
                        "x1": min(1.0, s["_x1"] / pw), "y1": min(1.0, s["_y1"] / ph),
                        "by": min(1.0, max(0.0, s["_by"] / ph)), "size": size, "text": s["text"].rstrip(),
                        "family": fam, "bold": bold, "italic": italic, "color": color, "edit": s["edit"]})
        return out
    finally:
        for h in (tp, page):
            try:
                if h is not None:
                    h.close()
            except Exception:
                pass


def text_paragraph(segs, i, page_w_pt=612.0, page_h_pt=792.0):
    """Indices of the runs that read as one paragraph with run `i`: stacked lines with the same left edge, the same size and
    ordinary line spacing. Always includes `i`; a lone line is a paragraph of one."""
    if not (0 <= i < len(segs)) or not segs[i].get("edit"):
        return [i] if 0 <= i < len(segs) else []

    def fits(a, b):
        em = max(a["size"], b["size"])
        dy = b["by"] - a["by"]
        same_look = (a["family"], a["bold"], a["italic"]) == (b["family"], b["bold"], b["italic"])
        return (b.get("edit") and same_look and abs(b["size"] - a["size"]) <= 0.06 * em
                and abs(b["x0"] - a["x0"]) * page_w_pt <= 0.8 * em and 0.8 * em <= dy * page_h_pt <= 2.5 * em)
    chain = [i]
    for direction in (1, -1):
        cur = segs[i]
        while len(chain) < 60:
            best = None
            for j, s in enumerate(segs):
                if j in chain:
                    continue
                a, b = (cur, s) if direction > 0 else (s, cur)
                if fits(a, b) and (best is None or abs(s["by"] - cur["by"]) < abs(segs[best]["by"] - cur["by"])):
                    best = j
            if best is None:
                break
            chain.append(best)
            cur = segs[best]
    return sorted(chain, key=lambda j: segs[j]["by"])


def text_box_from_segments(segs, idxs, page_w_pt, page_h_pt, cover="#ffffff"):
    """A TextBox that sits exactly on the runs `idxs` of a page: same words, size, colour and baseline, with the old words'
    rectangles recorded so saving can take them out (or cover them). Typing in it replaces the old text."""
    lines = sorted((segs[j] for j in idxs), key=lambda s: s["by"])
    first = lines[0]
    sizes = sorted(s["size"] for s in lines)
    size = round(sizes[len(sizes) // 2] * 10) / 10.0
    x0 = min(s["x0"] for s in lines)
    x1 = max(s["x1"] for s in lines)
    text = "\n".join(s["text"] for s in lines)
    w = min(1.0 - x0, (x1 - x0) + (0.012 + 0.5 * size / page_w_pt))
    spacing = 1.0
    base = TextBox(x=x0, y=first["y0"], w=max(TEXT_MIN_W, w), text=text, family=first["family"], size=size,
                   bold=first["bold"], italic=first["italic"], color=first["color"], cover=cover, orig=text)
    probe = _text_document(replace(base, text="Hg"), page_w_pt)
    natural = max(1.0, probe.size().height() / _TEXT_UNITS)
    if len(lines) > 1:
        pitches = sorted((b["by"] - a["by"]) * page_h_pt for a, b in zip(lines, lines[1:]))
        spacing = min(3.0, max(0.8, pitches[len(pitches) // 2] / natural))
    base = replace(base, spacing=spacing)
    first_doc = _text_document(replace(base, text=lines[0]["text"] or "H"), page_w_pt)   # keep the document alive: its layout dies with it
    first_doc.size()                                  # lays the text out; before that the block has no lines
    blk = first_doc.firstBlock()
    lay = blk.layout()
    ln = lay.lineAt(0) if lay.lineCount() else None
    ascent_pt = ((ln.position().y() + ln.ascent()) / _TEXT_UNITS) if ln is not None and ln.isValid() else size * 0.9
    y = first["by"] - ascent_pt / page_h_pt
    padx, pady = 0.7 / page_w_pt, 0.7 / page_h_pt
    erase = tuple((max(0.0, s["x0"] - padx), max(0.0, s["y0"] - pady), min(1.0, s["x1"] + padx), min(1.0, s["y1"] + pady))
                  for s in lines)
    h = text_box_height_frac(replace(base, y=0.0), page_w_pt, page_h_pt)
    return clean_text_box(replace(base, y=min(max(0.0, y), 1.0 - h), h=h, erase=erase))


# ---- taking the old words out of the page -------------------------------------------------------------
# Covering words with a rectangle leaves them in the file (selectable, searchable, in a text dump). When the old words are
# whole text objects of the page's own content, pdfium can drop exactly those objects and write the page back. That is only
# trusted after it is CHECKED: the page is drawn before and after, anything that changed outside the edited areas means the
# page is left alone and the old words are covered instead, and so is any rectangle that still has text under it.
TEXT_REMOVE_PAGE_DIFF_PX = 0.0004            # share of pixels outside the edited areas allowed to differ
TEXT_REMOVE_CONTENT_MAX = 16_000_000         # bytes of page content past which the page is not rewritten (covered instead)
TEXT_REMOVE_CHECK_PX = 4_000_000             # the before/after drawings are made no bigger than this


def rotate_frac_rect(rect, turn):
    """A fraction rectangle of a page, moved with the page when it is turned `turn` degrees clockwise."""
    a, b, c, d = rect
    turn = int(turn) % 360
    if turn == 90:
        return (1.0 - d, a, 1.0 - b, c)
    if turn == 180:
        return (1.0 - c, 1.0 - d, 1.0 - a, 1.0 - b)
    if turn == 270:
        return (b, 1.0 - c, d, 1.0 - a)
    return (a, b, c, d)


def _remove_text_on_page(doc, k, rects, tmp_dir):
    """Try to take the text objects inside `rects` (fractions of the page as shown) off page k of the open pdfium document.
    Returns (content_pdf_path or None, [bool removed per rect]). The caller holds _PDFIUM_LOCK."""
    import ctypes
    pdfium = _pdfium()
    raw = pdfium.raw
    none = (None, [False] * len(rects))
    page = doc[k]
    solo = None
    sp = None
    try:
        pw, ph = (float(v) for v in page.get_size())
        W, H = int(pw * 1000), int(ph * 1000)
        px, py = ctypes.c_double(), ctypes.c_double()

        def to_page(fx, fy):
            raw.FPDF_DeviceToPage(page.raw, 0, 0, W, H, 0, int(fx * W), int(fy * H), px, py)
            return px.value, py.value
        boxes = []
        for (a, b, c, d) in rects:
            (x0, y0), (x1, y1) = to_page(a, b), to_page(c, d)
            boxes.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        scale = min(1.5, (TEXT_REMOVE_CHECK_PX / max(1.0, pw * ph)) ** 0.5)
        before = page.render(scale=scale).to_pil().convert("RGB")
        solo = pdfium.PdfDocument.new()
        solo.import_pages(doc, [k])
        sp = solo[0]
        slack = 1.5
        gone = [False] * len(boxes)
        n_removed = 0
        for obj in list(sp.get_objects(filter=[raw.FPDF_PAGEOBJ_TEXT], max_depth=1)):
            l, b, r, t = obj.get_bounds()
            for j, (bl, bb, br, bt) in enumerate(boxes):
                if l >= bl - slack and r <= br + slack and b >= bb - slack and t <= bt + slack:
                    sp.remove_obj(obj)
                    obj.close()                       # detached for good: freed before its page goes away
                    n_removed += 1
                    gone[j] = True
                    break
        if not n_removed:
            return none
        sp.gen_content()
        after = sp.render(scale=scale).to_pil().convert("RGB")
        if after.size != before.size:
            return none
        # drawn before and after: nothing may differ outside the edited areas
        from PIL import ImageChops, ImageDraw
        mask = Image_new_mask(before.size)
        dr = ImageDraw.Draw(mask)
        sx, sy = before.size[0] / pw, before.size[1] / ph
        for (a, b, c, d) in rects:
            dr.rectangle([a * pw * sx - 4, b * ph * sy - 4, c * pw * sx + 4, d * ph * sy + 4], fill=0)
        diff = ImageChops.difference(before, after).convert("L").point(lambda v: 255 if v > 40 else 0)
        diff = ImageChops.multiply(diff, mask)
        bad = diff.histogram()[255]
        if bad > TEXT_REMOVE_PAGE_DIFF_PX * before.size[0] * before.size[1]:
            return none
        # and no word may be left under a rectangle that is reported as removed
        tp = sp.get_textpage()
        try:
            for j, (bl, bb, br, bt) in enumerate(boxes):
                if gone[j] and tp.get_text_bounded(bl + 0.5, bb + 0.5, br - 0.5, bt - 0.5).strip():
                    gone[j] = False
        finally:
            tp.close()
        if not any(gone):
            return none
        out = os.path.join(tmp_dir, f"page{k}.pdf")
        solo.save(out)
        return out, gone
    except Exception:
        return none
    finally:
        for h in (sp, solo, page):
            try:
                if h is not None:
                    h.close()
            except Exception:
                pass


def Image_new_mask(size):
    from PIL import Image
    return Image.new("L", size, 255)


# ---- signed documents -------------------------------------------------------------------------------
# A digital signature covers the exact bytes of the file as it was signed. Rebuilding the file (every Organizer
# save does) voids it. What does NOT void it is appending a new revision after the signed bytes: the signature
# still validates, and viewers show "changed after signing". Text added to a signed file therefore goes in as
# FreeText annotations in an appended revision, and only on the person's explicit say-so.
def _edit_audit(src_path: str, texts) -> str:
    """One line recorded INSIDE the edited file (document properties, never on a page): what made it, from which
    original, when. A person looking at the properties can tell it was edited and what it was edited from."""
    import hashlib
    h = hashlib.sha256()
    try:
        with open(src_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
    except OSError:
        digest = "unreadable"
    boxes = sum(len(t or ()) for t in texts or ())
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    return f"{APP_NAME} text edit | {boxes} text box(es) | source {os.path.basename(src_path)} sha256 {digest} | {now}"


def void_signature_values(path: str) -> int:
    """A rebuilt copy of a signed file carries the old signature VALUE, but the bytes it covered are gone, so every
    viewer would show a red 'signature invalid, document altered'. Take the value out of the copy (the field stays,
    empty) so the copy says nothing false. Returns how many values were removed."""
    pk = _pikepdf()
    tmp = path + ".nosig"
    removed = 0
    try:
        with pk.open(path) as pdf:
            acro = pdf.Root.get("/AcroForm")
            if acro is None:
                return 0

            def walk(fields, depth=0):
                nonlocal removed
                if depth > 8:
                    return
                for f in fields or []:
                    try:
                        if str(f.get("/FT", "")) == "/Sig" and "/V" in f:
                            del f["/V"]
                            removed += 1
                        if f.get("/Kids") is not None:
                            walk(f.Kids, depth + 1)
                    except Exception:
                        continue
            walk(acro.get("/Fields", []))
            if not removed:
                return 0
            if "/SigFlags" in acro:
                del acro["/SigFlags"]
            pdf.save(tmp)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return removed


def file_signature_state(path: str) -> str:
    """"signed" / "placeholder" / "none". A raw scan for /ByteRange comes first so a file with no signature never pays
    for a full parse."""
    try:
        with open(path, "rb") as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                if mm.find(b"/ByteRange") < 0 and mm.find(b"/Sig") < 0:
                    return "none"
            finally:
                mm.close()
    except (OSError, ValueError):
        return "none"
    try:
        with _pikepdf().open(path) as pdf:
            return _signature_state(pdf)
    except Exception:
        return "none"


def docmdp_level(path: str):
    """The certifying signer's rule for later changes: 1 none, 2 form filling and signing, 3 also annotations;
    None when the file is not certified. Text added after signing is an annotation, so it needs 3."""
    try:
        with _pikepdf().open(path) as pdf:
            ref = pdf.Root.Perms.DocMDP.Reference
            for r in ref:
                if str(r.get("/TransformMethod", "")) == "/DocMDP":
                    return int(r.TransformParams.get("/P", 2))
    except Exception:
        return None
    return None


def signed_text_options(path: str, plan, total_pages: int, multi: bool = False, texts=None) -> dict:
    """What can be done with text on `path`. {"signed": bool, "can_keep": bool, "why_not": str}.
    Keeping the signature needs the pages to stay exactly as they are."""
    state = file_signature_state(path)
    if state != "signed":
        return {"signed": False, "can_keep": False, "why_not": ""}
    if multi or not plan_is_unchanged(plan, total_pages):
        return {"signed": True, "can_keep": False,
                "why_not": "Pages were moved, turned, removed or added, which cannot be done without changing the signed file."}
    if any(b.erase for t in texts or () for b in (t or ())):
        return {"signed": True, "can_keep": False,
                "why_not": "Changing text that is already on a page rewrites that page, which cannot be done to a signed file."}
    level = docmdp_level(path)
    if level is not None and level < 3:
        return {"signed": True, "can_keep": False,
                "why_not": "The signer locked this document against added text and comments."}
    try:
        with _pikepdf().open(path) as pdf:
            if pdf.is_encrypted:
                return {"signed": True, "can_keep": False, "why_not": "The file is encrypted."}
    except Exception:
        return {"signed": True, "can_keep": False, "why_not": "The file could not be read."}
    return {"signed": True, "can_keep": True, "why_not": ""}


def _annot_matrix(turn: int):
    """Matrix that lays the overlay (drawn as the page is SHOWN) back into the page's own unrotated space."""
    return {0: (1, 0, 0, 1, 0, 0), 90: (0, 1, -1, 0, 0, 0), 180: (-1, 0, 0, -1, 0, 0), 270: (0, -1, 1, 0, 0, 0)}[turn % 360]


def _pdf_literal(text: str) -> bytes:
    return ("(" + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")").encode("latin-1", "replace")


def append_text_update(src_path: str, dest_path: str, per_page, audit: str = "") -> int:
    """Copy `src_path` to `dest_path` byte for byte and append one revision that adds the text boxes as FreeText
    annotations. `per_page` = [boxes] in page order. The signed bytes are never touched, so every signature in the
    file still validates. Returns the number of boxes written."""
    pk = _pikepdf()
    wanted = [k for k, boxes in enumerate(per_page) if boxes]
    if not wanted:
        raise PdfOpError("There is no text to add.")
    if any(b.erase for boxes in per_page for b in boxes):
        raise PdfOpError("Changing text that is already on a page rewrites that page, which cannot be done to a signed file.")
    with open(src_path, "rb") as f:
        raw = f.read()
    marks = list(re.finditer(rb"startxref\s+(\d+)\s*(?:%%EOF)?", raw))
    if not marks:
        raise PdfOpError("The signed file has no cross-reference table, so text cannot be added without damaging it.")
    prev = int(marks[-1].group(1))
    head = raw[prev:prev + 64].lstrip()
    xref_is_stream = not head.startswith(b"xref")
    over = dest_path + ".over"
    part = dest_path + ".sigpart"
    try:
        with pk.open(io.BytesIO(raw)) as pdf:
            if pdf.is_encrypted:
                raise PdfOpError("An encrypted signed file cannot take added text without damaging its signature.")
            if len(pdf.pages) != len(per_page):
                raise PdfOpError("The text could not be placed: the page count does not match.")
            orig_size = int(pdf.trailer.get("/Size", 0))
            pages_in, turns = [], []
            for k in wanted:
                page = pdf.pages[k]
                x0, y0, x1, y1 = _visible_box(page, k + 1)
                try:
                    turn = int(page.obj.get("/Rotate", 0)) % 360
                except Exception:
                    turn = 0
                if turn % 90:
                    turn = 0
                w, h = (y1 - y0, x1 - x0) if turn % 180 else (x1 - x0, y1 - y0)
                pages_in.append((w, h, 0, tuple(per_page[k])))
                turns.append((turn, (x0, y0, x1, y1)))
            text_overlay_pdf(pages_in, over)
            modified = {}
            now = datetime.datetime.now(datetime.timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
            with pk.open(over) as ov:
                for j, k in enumerate(wanted):
                    turn, (x0, y0, x1, y1) = turns[j]
                    w, h = pages_in[j][0], pages_in[j][1]
                    fx = pdf.copy_foreign(pk.Page(ov.pages[j]).as_form_xobject())
                    fx.BBox = pk.Array([0, 0, w, h])
                    fx.Matrix = pk.Array(list(_annot_matrix(turn)))
                    text = "\n".join(b.text for b in per_page[k] if b.text.strip())[:2000]
                    annot = pdf.make_indirect(pk.Dictionary(
                        Type=pk.Name.Annot, Subtype=pk.Name.FreeText,
                        Rect=pk.Array([x0, y0, x1, y1]), F=4, C=pk.Array([]),
                        Contents=pk.String(text), M=pk.String(now), T=pk.String(APP_NAME),
                        Subj=pk.String("Text added after signing"),
                        DA=pk.String("/Helv 12 Tf 0 g"), AP=pk.Dictionary(N=fx),
                        AcroDEdit=pk.String(audit or "text added after signing")))
                    page = pdf.pages[k].obj
                    cur = page.get("/Annots")
                    if cur is None:
                        arr = pdf.make_indirect(pk.Array([annot]))
                        page.Annots = arr
                        modified[page.objgen] = page
                    elif cur.is_indirect:
                        cur.append(annot)
                        modified[cur.objgen] = cur
                    else:
                        cur.append(annot)
                        modified[page.objgen] = page
            new_ids = []
            num = orig_size                                   # objects made now are numbered one after another from /Size
            while num < orig_size + 200000:
                try:
                    obj = pdf.get_object((num, 0))
                except Exception:
                    break
                if obj is None:
                    break
                new_ids.append((num, 0))
                num += 1
            if not new_ids and not modified:
                raise PdfOpError("The text could not be added.")
            for og in modified:
                if og[0] >= orig_size:
                    raise PdfOpError("The text could not be added: an unexpected object number clash.")
            todo = list(new_ids) + sorted(modified)
            body = bytearray(raw if raw.endswith((b"\n", b"\r")) else raw + b"\n")
            offsets = {}
            for og in todo:
                obj = pdf.get_object(og)
                offsets[og[0]] = (len(body), og[1])
                if isinstance(obj, pk.Stream):
                    data = obj.read_raw_bytes()
                    d = pk.Dictionary({str(k2): v for k2, v in obj.items()})
                    d["/Length"] = len(data)
                    body += f"{og[0]} {og[1]} obj\n".encode() + d.unparse() + b"\nstream\n" + data + b"\nendstream\nendobj\n"
                elif isinstance(obj, pk.Dictionary):
                    body += f"{og[0]} {og[1]} obj\n".encode() + pk.Dictionary({str(k2): v for k2, v in obj.items()}).unparse() + b"\nendobj\n"
                elif isinstance(obj, pk.Array):
                    body += f"{og[0]} {og[1]} obj\n".encode() + pk.Array(list(obj)).unparse() + b"\nendobj\n"
                elif isinstance(obj, bool):
                    body += f"{og[0]} {og[1]} obj\n{'true' if obj else 'false'}\nendobj\n".encode()
                elif isinstance(obj, (int, float)) or obj.__class__.__name__ == "Decimal":
                    body += f"{og[0]} {og[1]} obj\n{obj}\nendobj\n".encode()
                else:
                    body += f"{og[0]} {og[1]} obj\n".encode() + obj.unparse() + b"\nendobj\n"
            size = max(max(offsets), orig_size - 1) + 1
            root = pdf.trailer.Root.objgen
            extra = b""
            if "/Info" in pdf.trailer and pdf.trailer.Info.is_indirect:
                extra += b" /Info %d %d R" % pdf.trailer.Info.objgen
            if "/ID" in pdf.trailer:
                extra += b" /ID " + pdf.trailer.ID.unparse()
            nums = sorted(offsets)
            xref_at = len(body)
            if not xref_is_stream:
                body += b"xref\n"
                for n in nums:
                    off, gen = offsets[n]
                    body += b"%d 1\n%010d %05d n \n" % (n, off, gen)
                body += b"trailer\n<< /Size %d /Root %d %d R /Prev %d%s >>\n" % (size, root[0], root[1], prev, extra)
            else:
                xnum = size
                size += 1
                rows = bytearray()
                index = []
                for n in nums + [xnum]:
                    off, gen = offsets.get(n, (xref_at, 0))
                    index += [n, 1]
                    rows += bytes([1]) + off.to_bytes(4, "big") + gen.to_bytes(2, "big")
                body += (b"%d 0 obj\n<< /Type /XRef /Size %d /W [1 4 2] /Index [%s] /Root %d %d R /Prev %d%s /Length %d >>\nstream\n"
                         % (xnum, size, " ".join(map(str, index)).encode(), root[0], root[1], prev, extra, len(rows)))
                body += bytes(rows) + b"\nendstream\nendobj\n"
            body += b"startxref\n%d\n%%%%EOF\n" % xref_at
        with open(part, "wb") as f:
            f.write(body)
        if bytes(body[:len(raw)]) != raw:
            raise PdfOpError("The signed part of the file would have changed, so nothing was written.")
        with pk.open(part) as chk:                          # the finished file must parse and keep its page count
            if len(chk.pages) != len(per_page):
                raise PdfOpError("The text could not be added: the finished file has the wrong number of pages.")
        os.replace(part, dest_path)
    finally:
        for f in (over, part):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except OSError:
                pass
    return sum(len(per_page[k]) for k in wanted)


# ---------------------------------------------------------------------
# PDF reader engine: pure functions and small classes, no Qt. The reader view
# (PdfReaderView, further down) only draws what these compute, so every rule
# about zoom, which pages are on screen and how much memory the page cache may
# hold is testable without a window.
# ---------------------------------------------------------------------
READER_ZOOM_MIN, READER_ZOOM_MAX = 0.25, 4.0
READER_ZOOM_STEPS = (0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
READER_PAGE_GAP = 14                      # logical px between pages
READER_MARGIN = 24                        # logical px kept around a page by the fit modes
READER_MAX_PX, READER_MAX_SIDE = 20_000_000, 8000    # one rendered page, device pixels
READER_MEM_FILE_MAX = 96 * 1024 * 1024    # a PDF this small is read into memory once
READER_FIT_WIDTH, READER_FIT_PAGE = "width", "page"


def reader_clamp_zoom(z) -> float:
    try:
        z = float(z)
    except (TypeError, ValueError):
        return 1.0
    if z != z:                            # NaN
        return 1.0
    return min(READER_ZOOM_MAX, max(READER_ZOOM_MIN, z))


def reader_zoom_step(zoom: float, direction: int) -> float:
    """Next stop on the zoom ladder above (direction > 0) or below (< 0)
    `zoom`. Always moves, and never leaves the ladder's ends."""
    z = reader_clamp_zoom(zoom)
    if direction > 0:
        for s in READER_ZOOM_STEPS:
            if s > z + 1e-6:
                return s
        return READER_ZOOM_STEPS[-1]
    for s in reversed(READER_ZOOM_STEPS):
        if s < z - 1e-6:
            return s
    return READER_ZOOM_STEPS[0]


def reader_display_size(w_pt: float, h_pt: float, rot: int):
    """Size of a page as it is shown: a quarter turn swaps the sides."""
    def _sane(v):
        try:
            v = float(v)
        except Exception:
            return 612.0
        return 612.0 if v != v else min(max(v, 1.0), 14400.0)
    w, h = _sane(w_pt), _sane(h_pt)
    return (h, w) if int(rot) % 180 else (w, h)


def reader_fit_scale(mode, w_pt, h_pt, rot, view_w, view_h) -> float:
    """Logical px per PDF point. `mode` is "width", "page" or a zoom factor
    (1.0 = 100% = one logical pixel per point)."""
    if mode in (READER_FIT_WIDTH, READER_FIT_PAGE):
        dw, dh = reader_display_size(w_pt, h_pt, rot)
        aw = max(float(view_w) - 2 * READER_MARGIN, 40.0)
        ah = max(float(view_h) - 2 * READER_MARGIN, 40.0)
        s = aw / dw if mode == READER_FIT_WIDTH else min(aw / dw, ah / dh)
        return reader_clamp_zoom(s)
    return reader_clamp_zoom(mode)


class ReaderLayout:
    """Vertical page stack. `sizes` are the displayed (w, h) in points of each
    page IN PLAN ORDER; `scale` is logical px per point. Everything is a pure
    function of those two, so a reorder or rotate is just a new layout."""

    def __init__(self, sizes, scale, gap=READER_PAGE_GAP, pad=READER_MARGIN):
        self.scale, self.gap, self.pad = float(scale), int(gap), int(pad)
        self.widths, self.heights, self.tops = [], [], []
        y = self.pad
        for w, h in sizes:
            pw, ph = max(1, round(w * self.scale)), max(1, round(h * self.scale))
            self.widths.append(pw)
            self.heights.append(ph)
            self.tops.append(y)
            y += ph + self.gap
        self.total_height = (y - self.gap + self.pad) if self.tops else 2 * self.pad
        self.max_width = max(self.widths) if self.widths else 0

    def __len__(self):
        return len(self.tops)

    def visible(self, scroll_y, view_h, prefetch=1):
        """(first, last) page indices that touch the viewport, widened by
        `prefetch` pages each side. (0, -1) when there are no pages."""
        import bisect
        n = len(self.tops)
        if n == 0:
            return (0, -1)
        top, bottom = scroll_y, scroll_y + max(view_h, 1)
        first = max(0, bisect.bisect_right(self.tops, top) - 1)
        if self.tops[first] + self.heights[first] < top and first + 1 < n:
            first += 1
        last = min(n - 1, bisect.bisect_left(self.tops, bottom) - 1)
        last = max(last, first)
        return (max(0, first - prefetch), min(n - 1, last + prefetch))

    def current(self, scroll_y, view_h) -> int:
        """The page that owns the viewport: the one under its upper third,
        which is what a reader's 'page N of M' means while scrolling."""
        import bisect
        n = len(self.tops)
        if n == 0:
            return 0
        probe = scroll_y + max(view_h, 1) // 3
        i = bisect.bisect_right(self.tops, probe) - 1
        return min(n - 1, max(0, i))

    def scroll_for(self, index) -> int:
        if not self.tops:
            return 0
        i = min(len(self.tops) - 1, max(0, int(index)))
        return max(0, self.tops[i] - self.gap)


def reader_render_scale(w_pt, h_pt, scale, dpr=1.0) -> float:
    """pdfium scale (device px per point) for a page shown at `scale` logical
    px per point on a screen with device-pixel-ratio `dpr`, clamped by the same
    bomb guard the thumbnails use (a 2 x 14,400 pt page must not ask for GBs)."""
    return render_scale(w_pt, h_pt, float(scale) * max(float(dpr), 1.0),
                        READER_MAX_PX, READER_MAX_SIDE)


class ReaderCache:
    """LRU cache of rendered pages bounded by TOTAL PIXELS, not entry count: one
    page at 400% costs fifty at 50%. `get` refreshes recency. An entry bigger
    than the whole budget is refused rather than evicting everything for it."""

    def __init__(self, max_px):
        self.max_px = max(1, int(max_px))
        self._d = {}                      # insertion-ordered: oldest first
        self.px = 0

    def __len__(self):
        return len(self._d)

    def __contains__(self, key):
        return key in self._d

    def get(self, key):
        item = self._d.pop(key, None)
        if item is None:
            return None
        self._d[key] = item
        return item[0]

    def put(self, key, value, px) -> bool:
        px = max(1, int(px))
        old = self._d.pop(key, None)
        if old is not None:
            self.px -= old[1]
        if px > self.max_px:
            return False
        self._d[key] = (value, px)
        self.px += px
        while self.px > self.max_px and len(self._d) > 1:
            k = next(iter(self._d))
            self.px -= self._d.pop(k)[1]
        return True

    def drop_where(self, pred):
        for k in [k for k in self._d if pred(k)]:
            self.px -= self._d.pop(k)[1]

    def clear(self):
        self._d.clear()
        self.px = 0


class ReaderQueue:
    """Render requests for the reader's worker. Requests carry unique payloads
    (page, scale, rotation), so a 'busy' flag would drop real work: instead each
    request is keyed, a repeat of a queued key only re-prioritises it, anything
    from an older generation is discarded, and the nearest-to-the-viewport page
    is served first. Thread-safe; `pop` blocks until work, cancel or timeout."""

    def __init__(self):
        import threading as _t
        self._cv = _t.Condition()
        self._items = {}                  # key -> (priority, payload)
        self._gen = 0
        self._closed = False

    def push(self, gen, key, priority, payload):
        with self._cv:
            if self._closed or gen != self._gen:
                return False
            cur = self._items.get(key)
            if cur is None or priority < cur[0]:
                self._items[key] = (priority, payload)
            self._cv.notify()
            return True

    def new_generation(self) -> int:
        """Invalidate everything queued; returns the new generation number."""
        with self._cv:
            self._gen += 1
            self._items.clear()
            return self._gen

    @property
    def generation(self) -> int:
        return self._gen

    def prune(self, keep_keys):
        """Drop queued requests the viewport no longer wants."""
        with self._cv:
            for k in [k for k in self._items if k not in keep_keys]:
                del self._items[k]

    def pop(self, timeout=None):
        """-> (gen, key, payload) for the best request, or None (closed/timeout)."""
        with self._cv:
            while not self._items:
                if self._closed:
                    return None
                if not self._cv.wait(timeout):
                    return None
            key = min(self._items, key=lambda k: self._items[k][0])
            _prio, payload = self._items.pop(key)
            return (self._gen, key, payload)

    def close(self):
        with self._cv:
            self._closed = True
            self._items.clear()
            self._cv.notify_all()

    def pending(self) -> int:
        return len(self._items)


def reader_stale(opened_stamp, path) -> bool:
    """True when the file on disk is no longer the one the reader opened (or is
    gone), so the reader must say so instead of mixing old and new pages."""
    try:
        return source_stamp(path) != tuple(opened_stamp)
    except (OSError, TypeError):
        return True


def reader_hit_fraction(box, w_pt, h_pt):
    """Normalise a device-space box (x0, y0, x1, y1) in a w x h page into
    fractions of the displayed page, ordered and clipped to 0..1, so a highlight
    survives any zoom. None when the box is empty or entirely off the page."""
    x0, y0, x1, y1 = box
    w, h = max(float(w_pt), 1e-6), max(float(h_pt), 1e-6)
    fx0, fx1 = sorted((x0 / w, x1 / w))
    fy0, fy1 = sorted((y0 / h, y1 / h))
    fx0, fy0 = max(0.0, fx0), max(0.0, fy0)
    fx1, fy1 = min(1.0, fx1), min(1.0, fy1)
    if fx1 - fx0 <= 1e-6 or fy1 - fy0 <= 1e-6:
        return None
    return (fx0, fy0, fx1, fy1)


def pdf_page_sizes(path: str):
    """[(width_pt, height_pt)] as pdfium shows each page (its own /Rotate
    already applied), the same guard and lock as every other pdfium read."""
    require_safe_to_expand(path)
    pdfium = _pdfium()
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(path)
        n = len(doc)
    try:
        out = []
        for i in range(n):
            with _PDFIUM_LOCK:
                page = doc[i]
                try:
                    w, h = page.get_size()
                finally:
                    page.close()
            out.append((float(w), float(h)))
        return out
    finally:
        with _PDFIUM_LOCK:
            doc.close()


def reader_render_page(doc, index, scale, rotation):
    """Render one page of an open pdfium document to a PIL RGB image, rotated
    `rotation` degrees clockwise ON TOP of the page's own /Rotate (which is what
    the saved file will have). The caller holds _PDFIUM_LOCK."""
    page = doc[index]
    bitmap = None
    try:
        bitmap = page.render(scale=scale, rotation=int(rotation) % 360)
        return bitmap.to_pil().convert("RGB")
    finally:
        for handle in (bitmap, page):
            try:
                if handle is not None:
                    handle.close()
            except Exception:
                pass


def reader_find_boxes(doc, index, needle, rotation, limit=500):
    """Where `needle` (case-insensitive) sits on one page, as fractions of the
    page as DISPLAYED (after /Rotate and `rotation`). pdfium's own device
    conversion does the geometry, so a rotated or cropped page cannot put a
    highlight in the wrong place. The caller holds _PDFIUM_LOCK."""
    import ctypes
    pdfium = _pdfium()
    raw = pdfium.raw
    needle = (needle or "").strip()
    if not needle:
        return []
    quarter = (int(rotation) // 90) % 4
    page = doc[index]
    tp = None
    searcher = None
    try:
        pw, ph = page.get_size()
        dw, dh = (ph, pw) if quarter % 2 else (pw, ph)
        tp = page.get_textpage()
        searcher = tp.search(needle, match_case=False, match_whole_word=False)
        out = []
        while len(out) < limit:
            hit = searcher.get_next()
            if hit is None:
                break
            start, count = hit
            xs, ys = [], []
            for k in range(start, start + count):
                try:
                    l, b, r, t = tp.get_charbox(k, loose=False)
                except Exception:
                    continue
                for px, py in ((l, b), (r, t)):
                    dx, dy = ctypes.c_int(), ctypes.c_int()
                    raw.FPDF_PageToDevice(page.raw, 0, 0, int(dw * 1000), int(dh * 1000),
                                          quarter, px, py, dx, dy)
                    xs.append(dx.value / 1000.0)
                    ys.append(dy.value / 1000.0)
            if xs:
                box = reader_hit_fraction((min(xs), min(ys), max(xs), max(ys)), dw, dh)
                if box is not None:
                    out.append(box)
        return out
    finally:
        for handle in (searcher, tp, page):
            try:
                if handle is not None and hasattr(handle, "close"):
                    handle.close()
            except Exception:
                pass


def _reserve_destination(dest_path: str) -> str:
    """Claim a free name by creating it EXCLUSIVELY (O_EXCL), numbering as
    'name (1).pdf' when taken. Check-then-write let two saves of one source
    pick the same name and the second os.replace silently overwrote the
    first; the exclusive create makes the name ours or fails."""
    stem, ext = os.path.splitext(dest_path)
    for n in range(0, 1000):
        cand = dest_path if n == 0 else f"{stem} ({n}){ext}"
        try:
            with open(cand, "xb"):
                return cand
        except FileExistsError:
            continue
    raise PdfOpError("Too many files with that name already exist in the output folder.")


def _release_reservation(path):
    try:
        if path and os.path.exists(path) and os.path.getsize(path) == 0:
            os.remove(path)
    except OSError:
        pass


def _part_name(directory: str) -> str:
    # Short on purpose: dest + a suffix pushed a 244-character name past the
    # 255-character limit.
    return os.path.join(directory, f".{uuid.uuid4().hex[:10]}.part")


def _build_from_plan(reader, plan, dest_tmp):
    """Write the planned pages of `reader` to dest_tmp. Returns True when the
    source had bookmarks that were NOT carried (pages moved or removed, so
    their targets would be wrong). A rotate-only save keeps them."""
    _, PdfWriter = _pypdf()
    writer = PdfWriter()
    # append() keeps annotations, links and named destinations with their
    # page; a bare add_page loop drops them. Outlines are NOT imported.
    whole_in_order = [i for i, _r in plan] == list(range(len(reader.pages)))
    writer.append(reader, pages=[i for i, _r in plan], import_outline=whole_in_order)
    for k, (_i, rot) in enumerate(plan):
        if rot:
            writer.pages[k].rotate(rot)
    if reader.metadata is not None:
        writer.add_metadata(reader.metadata)
    with open(dest_tmp, "wb") as fh:
        writer.write(fh)
        fh.flush()
        os.fsync(fh.fileno())
    if whole_in_order:
        return False
    try:
        return bool(reader.outline)
    except Exception:
        return False


ORGANIZER_STRIP_ACTIVE = True       # Page Organizer saves drop scripts, launch actions and the like (links and page jumps stay)


def _strip_outline_actions(root, removed, cap=100_000):
    """Bookmarks can carry actions too: strip the active ones from every outline item (cycle- and size-safe)."""
    node = root.get("/Outlines")
    if node is None:
        return
    seen, stack, n = set(), [node], 0
    while stack and n < cap:
        cur = stack.pop()
        try:
            og = cur.objgen
            if og != (0, 0):
                if og in seen:
                    continue
                seen.add(og)
            n += 1
            _strip_active_holder(cur, removed)
            for key in ("/First", "/Next"):
                nxt = cur.get(key)
                if nxt is not None:
                    stack.append(nxt)
        except Exception:
            continue


def _strip_foreign_pages(path, positions, document=False):
    """Remove programs from the pages at `positions` (0-based, in the file at `path`): launch / script /
    submit / open-file actions, movie and 3-D annotations and attached files. Links and page jumps stay. Form
    fields stay fillable. `document=True` also clears the document's own open actions, document scripts and
    bookmark actions. The file is only rewritten when something was found. Fails closed: if the file cannot
    be cleaned it is not saved. Returns {what: count}."""
    if not positions and not document:
        return {}
    pk = _pikepdf()
    removed = {}
    tmp = path + ".clean"
    try:
        with pk.open(path) as pdf:
            for k in positions:
                page = pdf.pages[k].obj
                for key in ("/AA", "/AF"):
                    if key in page:
                        del page[key]
                        _bump(removed, "Automatic actions")
                annots = page.get("/Annots")
                if annots is None:
                    continue
                keep, dropped = [], 0
                for a in annots:
                    sub = str(a.get("/Subtype", ""))
                    if sub in _ACTIVE_ANNOTS or sub == "/FileAttachment":
                        _bump(removed, "Comments and markups")
                        dropped += 1
                        continue
                    holder, depth = a, 0
                    while holder is not None and depth < 8:      # a widget's script can sit on its parent field
                        _strip_active_holder(holder, removed)
                        holder = holder.get("/Parent")
                        depth += 1
                    keep.append(a)
                if dropped:
                    page.Annots = pdf.make_indirect(pk.Array(keep))
            if document:
                root = pdf.Root
                act = root.get("/OpenAction")
                if act is not None and hasattr(act, "keys") and _action_is_active(act):
                    del root["/OpenAction"]
                    _bump(removed, "Automatic actions")
                if "/AA" in root:
                    del root["/AA"]
                    _bump(removed, "Automatic actions")
                names = root.get("/Names")
                if names is not None and "/JavaScript" in names:
                    del names["/JavaScript"]
                    _bump(removed, "Document JavaScript")
                _strip_outline_actions(root, removed)
            if removed:
                pdf.save(tmp)
        if removed:
            os.replace(tmp, path)
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise PdfOpError("A page could not be checked for programs, "
                         f"so nothing was written. (Details: {e})") from e
    return removed


def _build_from_multi_plan(readers, plan, dest_tmp, removed=None):
    """Write plan = [(source, page, rotation)] using `readers` (source 0 first). Pages that come
    from a source other than 0 are cleaned of programs. Returns True when a used source had
    bookmarks (they cannot be carried into a mixed file)."""
    _, PdfWriter = _pypdf()
    writer = PdfWriter()
    run_sid, run = None, []
    def flush():
        if run:
            writer.append(readers[run_sid], pages=list(run), import_outline=False)
    for sid, idx, _rot in plan:
        if sid != run_sid:
            flush()
            run_sid, run = sid, []
        run.append(idx)
    flush()
    for k, (_sid, _idx, rot) in enumerate(plan):
        if rot:
            writer.pages[k].rotate(rot)
    if readers[0].metadata is not None:
        writer.add_metadata(readers[0].metadata)
    with open(dest_tmp, "wb") as fh:
        writer.write(fh)
        fh.flush()
        os.fsync(fh.fileno())
    gone = _strip_foreign_pages(dest_tmp, [k for k, (sid, _i, _r) in enumerate(plan) if sid != 0])
    for what, n in gone.items():
        _bump(removed if removed is not None else {}, what, n)
    dropped = False
    for sid in {sid for sid, _i, _r in plan}:
        try:
            dropped = dropped or bool(readers[sid].outline)
        except Exception:
            pass
    return dropped


def _check_unchanged(src_path, expect):
    if expect is not None:
        try:
            now = source_stamp(src_path)
        except OSError as e:
            raise PdfOpError(describe_lock_failure(src_path, e)) from e
        if tuple(now) != tuple(expect):
            raise PdfOpError(
                f"'{os.path.basename(src_path)}' changed on disk after it was opened here. "
                "Nothing was written - reopen the file and redo the edit.")


def organize_pdf(src_path: str, plan, expect=None, replace: bool = False,
                 out_dir_name: str = None, extra=None, extra_expect=None, texts=None, keep_signatures: bool = False,
                 strip_own: bool = True):
    """Write the planned pages of src_path in the planned order and rotation.

    Default (replace=False): a new file with the same name goes into an
    'Reorganized' folder beside the source; an existing file is never
    overwritten (a numbered name is used). replace=True puts the result at
    the original path and keeps the original in <Reorganized>/Originals first.

    `texts` = one tuple of TextBox per planned page (same order as `plan`). A save that adds text is
    ALWAYS written as a new '<name>_edited.pdf' beside the others and never replaces the original.
    `keep_signatures` (only with text, a signed file and an unchanged page plan) appends the text as a new revision
    after the signed bytes instead of rebuilding the file, so the signature stays valid.

    Returns {"path", "pages", "dropped_bookmarks", "backup", "edited"}.
    """
    reader = _open_reader(src_path)
    total = len(reader.pages)
    has_text = bool(texts) and any(texts)
    multi = is_multi_plan(plan)
    readers = [reader]
    if multi:
        extra = list(extra or [])
        if len(extra) + 1 > MULTI_SOURCES_MAX:
            raise PdfOpError(f"Pages from more than {MULTI_SOURCES_MAX} different files cannot be combined in one save.")
        for other in extra:                                  # foreign files get every guard the first one got
            require_safe_to_expand(other)
            readers.append(_open_reader(other))
        plan = validate_multi_plan(plan, [len(r.pages) for r in readers])
        if not has_text and multi_plan_is_unchanged(plan, total):
            raise PdfOpError("Nothing was changed, so nothing was saved.")
        _check_unchanged(src_path, expect)
        for other, stamp in zip(extra, list(extra_expect or []) + [None] * len(extra)):
            _check_unchanged(other, stamp)
    else:
        plan = validate_page_plan(plan, total)
        if not has_text and plan_is_unchanged(plan, total):
            raise PdfOpError("Nothing was changed, so nothing was saved.")
        _check_unchanged(src_path, expect)
    if has_text and len(texts) != len(plan):
        raise PdfOpError("The text list does not match the pages, so nothing was written.")
    if has_text:
        n_boxes = sum(len(t or ()) for t in texts)
        n_chars = sum(len(b.text) for t in texts for b in (t or ()))
        if n_boxes > TEXT_BOXES_TOTAL_MAX or n_chars > TEXT_CHARS_TOTAL_MAX:
            raise PdfOpError(f"There is more text than one save can take ({TEXT_BOXES_TOTAL_MAX:,} boxes or "
                             f"{TEXT_CHARS_TOTAL_MAX:,} characters), so nothing was saved.")

    src_dir = os.path.dirname(os.path.abspath(src_path))
    folder = os.path.join(src_dir, out_dir_name or setting("organized_dir_name"))
    os.makedirs(folder, exist_ok=True)
    name = os.path.basename(src_path)
    if has_text:
        replace = False                                   # edited text is evidence of a change: keep the original untouched
        stem, ext = os.path.splitext(name)
        name = f"{stem}_edited{ext or '.pdf'}"
    dest = None
    backup = None
    if replace:
        tmp = _part_name(src_dir)
    else:
        dest = _reserve_destination(os.path.join(folder, name))
        tmp = _part_name(folder)
    signed_source = file_signature_state(src_path) == "signed"
    kept = False
    active = {}                                           # programs taken out of the saved file: {what: count}
    text_report = {"text_removed": 0, "text_covered": 0}
    audit = _edit_audit(src_path, texts) if has_text else ""
    try:
        if has_text and keep_signatures:
            opts = signed_text_options(src_path, plan, total, multi, texts)
            if not opts["can_keep"]:
                raise PdfOpError(opts["why_not"] or "This file has no signature to keep.")
            append_text_update(src_path, tmp, [tuple(t or ()) for t in texts], audit=audit)
            dropped, kept = False, True
        else:
            try:
                dropped = (_build_from_multi_plan(readers, plan, tmp, active) if multi
                           else _build_from_plan(reader, plan, tmp))
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
                raise PdfOpError(f"'{os.path.basename(src_path)}' has damaged pages that cannot be rebuilt, so "
                                 f"nothing was saved. Try Optimize or re-export it from the program that made it. "
                                 f"(Details: {type(e).__name__}: {e})") from e
            if ORGANIZER_STRIP_ACTIVE and strip_own:      # a saved copy never carries scripts or launch actions along
                own = [k for k, item in enumerate(plan) if not multi or item[0] == 0]
                for what, n in _strip_foreign_pages(tmp, own, document=True).items():
                    _bump(active, what, n)
            if has_text:
                apply_text_overlays(tmp, [(item[-1], tuple(t or ())) for item, t in zip(plan, texts)], audit=audit,
                                    report=text_report)
            if signed_source:
                void_signature_values(tmp)
        if replace:
            keep_dir = os.path.join(folder, "Originals")
            os.makedirs(keep_dir, exist_ok=True)
            backup = _reserve_destination(os.path.join(keep_dir, name))
            # The original is copied BEFORE it is replaced: if the copy
            # fails nothing has been touched. The copy is checked, not assumed.
            shutil.copy2(src_path, backup)
            if os.path.getsize(backup) != os.path.getsize(src_path):
                raise PdfOpError("The backup copy of the original is incomplete, "
                                 "so nothing was changed.")
            _check_unchanged(src_path, expect)
            os.replace(tmp, src_path)
            dest = src_path
        else:
            os.replace(tmp, dest)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        if not replace:
            _release_reservation(dest)
        _release_reservation(backup)
        raise
    return {"path": dest, "pages": len(plan), "dropped_bookmarks": dropped,
            "backup": backup, "was_encrypted": bool(reader.is_encrypted), "edited": has_text,
            "signature_kept": kept, "signature_removed": bool(signed_source and not kept),
            "text_removed": text_report["text_removed"], "text_covered": text_report["text_covered"],
            "active_removed": active}


def extract_pages(src_path: str, indices, expect=None, out_dir_name: str = None,
                  dest_dir: str = None, file_name: str = None):
    """Copy the chosen pages (kept in the order given) into a NEW PDF. The
    source is untouched. The panel always asks; by default (no dialog) the file is '<name>_Extract.pdf' in the
    Reorganized folder; `dest_dir` and `file_name` (with or without '.pdf')
    choose where and what instead. An existing file is never overwritten:
    the name is numbered ('name (1).pdf')."""
    reader = _open_reader(src_path)
    total = len(reader.pages)
    plan = validate_page_plan([(i, 0) for i in indices], total)
    _check_unchanged(src_path, expect)
    if dest_dir:
        folder = os.path.abspath(dest_dir)
        if not os.path.isdir(folder):
            raise PdfOpError("The folder you chose for the extracted pages is not there.")
    else:
        folder = os.path.join(os.path.dirname(os.path.abspath(src_path)),
                              out_dir_name or setting("organized_dir_name"))
        os.makedirs(folder, exist_ok=True)
    if file_name is not None:
        stem = file_name.strip()
        if stem.lower().endswith(".pdf"):
            stem = stem[:-4]
        if not valid_name_component(stem):
            raise PdfOpError(f"'{file_name}' cannot be used as a file name.")
    else:
        stem = os.path.splitext(os.path.basename(src_path))[0] + "_Extract"
    dest = _reserve_destination(os.path.join(folder, f"{stem}.pdf"))
    tmp = _part_name(folder)
    try:
        _build_from_plan(reader, plan, tmp)
        if file_signature_state(src_path) == "signed":
            void_signature_values(tmp)
        os.replace(tmp, dest)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        _release_reservation(dest)
        raise
    return {"path": dest, "pages": len(plan)}


def natural_name_key(name: str):
    """Sort key that puts 'a2.pdf' before 'a10.pdf' (the order the list uses)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def pdf_folder_listing(folder: str):
    """The PDFs directly inside `folder` (not subfolders, so the Reorganized
    output folder is never listed),
    natural-sorted. Each: {path, name, size, mtime_ns}. Unreadable entries are skipped."""
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        full = os.path.join(folder, n)
        if n.lower().endswith(".pdf") and os.path.isfile(full):
            try:
                st = os.stat(full)
                out.append({"path": full, "name": n, "size": st.st_size,
                            "mtime_ns": st.st_mtime_ns})
            except OSError:
                continue
    out.sort(key=lambda d: natural_name_key(d["name"]))
    return out


# ---------------------------------------------------------------------
# Decompression-bomb pre-flight.
#
# MEASURED 2026-10-12: a 1 MB PDF whose content stream inflates to 1 GiB made
# Find-in-folder take 15 s and 2.1 GB of RAM; a 4 MB one took 22 s and 5.2 GB,
# and inspect on its 4 GiB sibling was killed by the OS (which in the .exe is
# the whole program). pdfium, pikepdf and pypdf all inflate a stream in full,
# so the only place to stop it is BEFORE any of them is handed the file.
#
# DESIGN (after an adversarial review broke the first two versions): nothing
# the file says about itself is trusted. Dictionaries, /Filter, /Length,
# /Subtype and 'endstream' are all forgeable, so none of them decides
# anything. The scan finds every `stream` keyword and simply TRIES the data:
# a zlib stream announces itself (header check) and ends itself, a stack of
# zlib layers is followed layer by layer, and every byte produced is counted,
# 1 MB at a time, so memory stays near 1 MB however large the lie is.
# Filter names are used only as HINTS to also try the ASCII85/Hex and LZW
# wrappers. Encrypted files are read decrypted (still not inflated) via qpdf.
# Cost: ~16 ms for a 300-page file, ~0.3 ms for a small one, once per file
# per session (cached on path+size+mtime).
# ---------------------------------------------------------------------
BOMB_STREAM_MAX = 128 * 1024 * 1024        # what ONE stream (all its zlib layers) may inflate to
BOMB_TOTAL_MAX = 2 * 1024 * 1024 * 1024    # bytes read + produced by the whole scan of one file (~5 s of library work)
BOMB_SHARED_MIN = 64 * 1024 * 1024         # a stream this big may be one page's content shared by every page
BOMB_LAYERS_MAX = 12
BOMB_IMAGE_MAX = 1024 * 1024 * 1024        # optimizer: the most it will decode for one image
_BOMB_STREAM_RE = re.compile(rb"(?<!end)stream[ \t\x00\x0c]*(?:\r\n|\n|\r)")
_BOMB_COUNT_RE = re.compile(rb"/Count\s+(\d+)")
_BOMB_HINT_RE = re.compile(rb"LZW|ASCII85|A85|ASCIIHex|AHx")
_BOMB_OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")
_BOMB_CACHE = {}


class _BombOver(Exception):
    pass


class _BombStats:
    def __init__(self):
        self.total = 0
        self.stream = 0
        self.biggest = 0
        self.ascii_left = 2 << 20              # pure-Python ASCII85 is slow: look at a prefix, within a per-file budget

    def add(self, n: int, produced: bool = True):
        self.total += n
        if produced:
            self.stream += n
            self.biggest = max(self.biggest, self.stream)
        if self.stream > BOMB_STREAM_MAX or self.total > BOMB_TOTAL_MAX:
            raise _BombOver()


def bomb_message(name: str) -> str:
    return (f"{name} is built to expand to an enormous size when it is read, so the program "
            "will not open it. This is a safety check, not a damaged file. If you trust where it "
            "came from, open it in a PDF viewer instead.")


def _zlib_header(b0: int, b1: int) -> bool:
    return (b0 & 0x0F) == 8 and ((b0 << 8) | b1) % 31 == 0 and not (b1 & 0x20)


def _pieces(buf, start: int, stats: "_BombStats"):
    """The bytes from `start` on, 4 KB then 64 KB at a time, each counted as read."""
    pos, n, size = start, len(buf), 4096
    while pos < n:
        piece = bytes(buf[pos:pos + size]); pos += size; size = 65536
        stats.add(len(piece), produced=False)
        yield piece


def _inflate_gen(src, stats: "_BombStats"):
    """One zlib layer: yields its output in pieces of at most 1 MB, counting every byte."""
    d, step = zlib.decompressobj(), 1 << 20
    for piece in src:
        buf = piece
        while True:
            out = d.decompress(buf, step)
            if out:
                stats.add(len(out))
                yield out
            buf = d.unconsumed_tail
            if d.eof:
                return
            if not buf and len(out) < step:
                break


def _consume(src, stats: "_BombStats", depth: int = 0) -> None:
    """Follow a chain of zlib layers, counting what each produces. Anything that is not
    a zlib header ends the chain (its size was already counted by the layer above)."""
    it = iter(src)
    first = next(it, b"")
    if depth >= BOMB_LAYERS_MAX or len(first) < 2 or not _zlib_header(first[0], first[1]):
        if depth > 0:
            for _ in it:
                pass                            # drive the layers above so they are counted
        return
    import itertools
    _consume(_inflate_gen(itertools.chain((first,), it), stats), stats, depth + 1)


def _ascii_to_bytes(buf, start: int, kind: str, stats: "_BombStats"):
    """Undo an ASCIIHex / ASCII85 wrapper; cost is bounded by the text actually present."""
    import base64, binascii
    end_mark = b">" if kind == "hex" else b"~>"
    take = min(1 << 20, stats.ascii_left)      # 1 MB of text is ~800 KB of data: a bomb (ratio ~1000) shows itself in 260 KB
    if take < 64:
        return None
    end = buf.find(end_mark, start, start + take)
    end = start + take if end < 0 else end
    text = bytes(buf[start:end]); stats.add(len(text), produced=False); stats.ascii_left -= len(text)
    try:
        if kind == "hex":
            digits = re.sub(rb"[^0-9A-Fa-f]", b"", text)
            return binascii.unhexlify(digits + b"0" * (len(digits) % 2))
        text = text.lstrip()
        text = text[2:] if text.startswith(b"<~") else text
        return base64.a85decode(text, ignorechars=b" \t\n\r\v\f")
    except (binascii.Error, ValueError):
        return None


def _lzw_count(buf, start: int, stats: "_BombStats") -> None:
    """Output size an LZW stream (PDF flavour: MSB first, 9-12 bit, EarlyChange 1) would decode to,
    counted without building it. Only the first 4 MB of input is read: a bomb reaches the cap in ~100 KB."""
    data = bytes(buf[start:start + (4 << 20)]); stats.add(len(data), produced=False)
    lens = [1] * 256 + [0, 0]
    width, bits, acc, prev = 9, 0, 0, None
    for byte in data:
        acc = (acc << 8) | byte; bits += 8
        while bits >= width:
            code = (acc >> (bits - width)) & ((1 << width) - 1)
            bits -= width; acc &= (1 << bits) - 1
            if code == 256:
                lens = lens[:258]; width = 9; prev = None
                continue
            if code == 257:
                return
            if code < len(lens):
                size = lens[code]
            elif code == len(lens) and prev is not None:
                size = lens[prev] + 1
            else:
                return                          # not LZW
            if prev is not None and len(lens) < 4096:
                lens.append(lens[prev] + 1)
            stats.add(size)
            prev = code
            n = len(lens) + 1
            width = 9 if n < 512 else 10 if n < 1024 else 11 if n < 2048 else 12


def _scan_one(buf, start: int, hints: set, stats: "_BombStats") -> None:
    """Try one stream every way its neighbourhood hints it might be encoded. A decoder that rejects
    the data (zlib.error) just means 'not that encoding'; only exceeding a cap raises _BombOver."""
    def attempt(fn, *args):
        stats.stream = 0
        try:
            fn(*args)
        except zlib.error:
            pass

    attempt(lambda: _consume(_pieces(buf, start, stats), stats))
    for kind in ("hex", "a85"):
        if kind in hints:
            body = _ascii_to_bytes(buf, start, kind, stats)
            if body:
                attempt(lambda: _consume(_pieces(body, 0, stats), stats))
    if "lzw" in hints:
        attempt(_lzw_count, buf, start, stats)


def _hints_of(text: str):
    t = text
    out = set()
    if "LZW" in t:
        out.add("lzw")
    if "ASCII85" in t or "A85" in t:
        out.add("a85")
    if "ASCIIHex" in t or "AHx" in t:
        out.add("hex")
    return out


def _check_shared(data, stats: "_BombStats") -> None:
    """One big stream can be the /Contents of every page, and a library inflates it once PER PAGE.
    Charge the biggest stream once for each page the file claims (/Count)."""
    if stats.biggest <= BOMB_SHARED_MIN:
        return
    pages = max((int(m.group(1)) for m in _BOMB_COUNT_RE.finditer(data) if len(m.group(1)) < 7), default=1)
    if stats.biggest * max(pages, 1) > BOMB_TOTAL_MAX:
        raise _BombOver()


def _is_encrypted(data) -> bool:
    return data.find(b"/Encrypt") >= 0         # anywhere: a false positive only costs the qpdf route below


def _scan_for_bombs(data, name: str, path: str = None):
    """Refusal text, or None. Encrypted files hold encrypted streams that every PDF library opens
    with the empty password, so their stream bytes are read DECRYPTED (not inflated) through qpdf."""
    stats = _BombStats()
    pdf = None
    if _is_encrypted(data):
        try:
            pdf = _pikepdf().open(path or io.BytesIO(bytes(data)))
        except Exception:
            pdf = None                          # wrong password or damaged: the real open will say so
    try:
        if pdf is not None:
            pk = _pikepdf()
            for h in _BOMB_OBJ_RE.finditer(data):
                try:
                    obj = pdf.get_object((int(h.group(1)), int(h.group(2))))
                    if not isinstance(obj, pk.Stream):
                        continue
                    raw = obj.read_raw_bytes()
                    hints = _hints_of(str(obj.get("/Filter", "")))
                except Exception:
                    continue
                _scan_one(raw, 0, hints, stats)
            _check_shared(data, stats)
            return None
        import bisect
        marks = [(m.start(), m.group()) for m in _BOMB_HINT_RE.finditer(data)]
        pos = [x[0] for x in marks]
        for m in _BOMB_STREAM_RE.finditer(data):
            k = bisect.bisect_left(pos, m.start() - 65536)
            near = {marks[i][1].decode("latin-1") for i in range(k, len(pos)) if pos[i] < m.start()}
            _scan_one(data, m.end(), _hints_of(" ".join(near)), stats)
        _check_shared(data, stats)
        return None
    except _BombOver:
        return bomb_message(name)
    finally:
        if pdf is not None:
            pdf.close()


def _inflate_capped(data, start: int, cap: int) -> int:
    """Inflated size of ONE zlib stream beginning at data[start], stopped one 1 MB step past `cap`
    (used by the optimizer's own image check; the pre-flight uses _scan_one)."""
    d, total, pos, n = zlib.decompressobj(), 0, start, len(data)
    step = 1 << 20
    try:
        while pos < n and not d.eof and total <= cap:
            buf = bytes(data[pos:pos + 65536]); pos += 65536
            while True:
                out = d.decompress(buf, step)
                total += len(out)
                buf = d.unconsumed_tail
                if total > cap or d.eof or (not buf and len(out) < step):
                    break
    except zlib.error:
        pass                                   # not zlib after all, or truncated: not a bomb
    return total


def pdf_expansion_problem(path: str):
    """None when the file is safe to hand to a PDF library; otherwise the plain-language
    reason it is refused. Never raises: an unreadable file is the caller's error to report.
    Also accepts the file's bytes (the Optimizer hands the flattener the bytes it already
    read); those are scanned directly and not cached."""
    if isinstance(path, (bytes, bytearray, memoryview)):
        try:
            return _scan_for_bombs(bytes(path), "this PDF")
        except OSError:
            return None
    try:
        st = os.stat(path)
        key = (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)
    except OSError:
        return None
    if key in _BOMB_CACHE:
        return _BOMB_CACHE[key]
    refusal = pdf_file_problem(path)
    if refusal:
        return refusal
    try:
        with open(path, "rb") as fh:
            try:
                data = fh.read() if st.st_size < (4 << 20) else mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            except (ValueError, OSError):      # empty file, or a share that cannot be mapped
                data = fh.read()
            try:
                problem = _scan_for_bombs(data, os.path.basename(path), path)
            finally:
                if hasattr(data, "close"):
                    data.close()
    except OSError:
        return None
    if len(_BOMB_CACHE) >= 512:
        _BOMB_CACHE.clear()
    _BOMB_CACHE[key] = problem
    return problem


def require_safe_to_expand(path: str) -> None:
    """Raise PdfOpError (plain language) when the file is a decompression bomb."""
    problem = pdf_expansion_problem(path)
    if problem:
        raise PdfOpError(problem)


def pdf_page_texts(path: str):
    """One lowercase, whitespace-collapsed string per page from the PDF's own
    text layer (no OCR). A scan with no text layer yields empty strings."""
    require_safe_to_expand(path)
    pdfium = _pdfium()
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(path)
        total = len(doc)
    try:
        out = []
        for i in range(total):
            with _PDFIUM_LOCK:
                page = doc[i]
                try:
                    tp = page.get_textpage()
                    try:
                        out.append(" ".join(tp.get_text_range().lower().split()))
                    finally:
                        tp.close()
                finally:
                    page.close()
        return out
    finally:
        with _PDFIUM_LOCK:
            doc.close()


def search_pdf_text(path: str, needle: str):
    """-> {"pages": [0-based page indices with a hit], "hits": total matches,
    "has_text": bool}. Plain case-insensitive substring; blank needle = no hits."""
    needle = " ".join((needle or "").lower().split())
    texts = pdf_page_texts(path)
    has_text = any(texts)
    if not needle:
        return {"pages": [], "hits": 0, "has_text": has_text}
    pages = [i for i, t in enumerate(texts) if needle in t]
    return {"pages": pages, "hits": sum(texts[i].count(needle) for i in pages),
            "has_text": has_text}


def merge_record_path() -> str:
    """Says which file slot 0 of the merge backups belongs to, and what that
    file looked like right after the merge - so Undo can refuse to restore
    over work done to it since."""
    return os.path.join(_log_dir(), "last_merge_backup.json")


def _merge_record_clear() -> None:
    try:
        os.remove(merge_record_path())
    except OSError:
        pass


def _merge_record_write(base_path: str) -> None:
    try:
        st = os.stat(base_path)
        rec = {"original": os.path.abspath(base_path), "size": st.st_size,
               "mtime_ns": st.st_mtime_ns,
               "backup_size": os.path.getsize(merge_backup_path())}
        _atomic_write_text(merge_record_path(), json.dumps(rec))
    except (OSError, ValueError):
        _merge_record_clear()


def merge_undo_info():
    """The last merge Undo could take back, as {"original", ...}, or None."""
    try:
        with open(merge_record_path(), "r", encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if not (isinstance(rec, dict) and isinstance(rec.get("original"), str)
            and isinstance(rec.get("size"), int) and isinstance(rec.get("mtime_ns"), int)
            and isinstance(rec.get("backup_size"), int)):
        return None
    return rec


def undo_last_merge() -> str:
    """Put the file the last merge overwrote back as it was. Refuses, with the
    reason in plain words, when the file was changed since or the backup is not
    intact; the backup itself is kept either way. Returns the restored path."""
    rec = merge_undo_info()
    if rec is None:
        raise PdfOpError("There is no merge to undo.")
    path, backup = rec["original"], merge_backup_path()
    name = os.path.basename(path)
    if not os.path.isfile(backup) or os.path.getsize(backup) != rec["backup_size"]:
        raise PdfOpError(f"The saved copy of '{name}' is missing or damaged, so it cannot be restored.")
    try:
        st = os.stat(path)
    except OSError:
        raise PdfOpError(f"'{name}' is no longer where the merge left it. "
                         f"The saved copy is still at {backup}.")
    if (st.st_size, st.st_mtime_ns) != (rec["size"], rec["mtime_ns"]):
        raise PdfOpError(f"'{name}' was changed after the merge, so restoring would lose those changes. "
                         f"The saved copy is still at {backup}.")
    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        shutil.copyfile(backup, tmp)
        with open(tmp, "r+b") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _merge_record_clear()
    return path


def merge_backup_path(slot: int = 0) -> str:
    """Backup of the last file the merger overwrote. Slot 0 is the newest;
    Settings > Advanced decides how many slots are kept."""
    name = "last_merge_backup.pdf" if slot == 0 else f"last_merge_backup.{slot}.pdf"
    return os.path.join(_log_dir(), name)


def _rotate_merge_backups(keep: int) -> None:
    """Shift slot 0 -> 1 -> ... -> keep-1; anything past the last slot goes."""
    keep = max(1, int(keep))
    for slot in range(keep - 1, 0, -1):
        older, newer = merge_backup_path(slot), merge_backup_path(slot - 1)
        if os.path.exists(newer):
            try:
                os.replace(newer, older)
            except OSError:
                pass
    # Slots beyond `keep` from an earlier, larger setting.
    for slot in range(keep, 11):
        try:
            os.remove(merge_backup_path(slot))
        except OSError:
            pass


def _same_file(a: str, b: str) -> bool:
    try:
        if os.path.samefile(a, b):
            return True
    except OSError:
        pass
    return (os.path.normcase(os.path.realpath(a))
            == os.path.normcase(os.path.realpath(b)))


def merge_onto(base_path: str, ref_path: str) -> None:
    """Append ref_path's pages onto base_path, overwriting base_path in place.

    The overwrite is deliberate. What is NOT deliberate, and was possible
    before this guard, is dropping the reference file onto itself: that
    appended the document to itself and destroyed the original while
    reporting success. Reproduced on a 2-page file -> 4 pages.
    """
    if _same_file(base_path, ref_path):
        raise PdfOpError(
            f"'{os.path.basename(base_path)}' is the same file as the "
            "reference PDF. Merging it onto itself would double it and "
            "destroy the original, so nothing was changed."
        )

    base_reader = _open_reader(base_path)
    ref_reader = _open_reader(ref_path)
    base_pages = len(base_reader.pages)
    ref_pages = len(ref_reader.pages)
    if base_pages == 0 or ref_pages == 0:
        raise PdfOpError(
            f"'{os.path.basename(base_path if base_pages == 0 else ref_path)}'"
            " has no pages, so there is nothing to merge."
        )

    _, PdfWriter = _pypdf()
    writer = PdfWriter()
    writer.append(base_reader, import_outline=False)
    writer.append(ref_reader, import_outline=False)
    if base_reader.metadata is not None:
        writer.add_metadata(base_reader.metadata)

    # Report success only against a counted result, not against the absence
    # of an exception.
    if len(writer.pages) != base_pages + ref_pages:
        raise PdfOpError(
            "The merged document did not come out the expected length, so "
            "the original was left alone."
        )

    # This is the app's only destructive in-place write. Keep one recoverable
    # copy - the Renamer has undo and the Optimizer never overwrites, so the
    # merger was the only operation with no way back.
    backed_up = False
    try:
        _rotate_merge_backups(setting("merge_backups"))
        shutil.copy2(base_path, merge_backup_path())
        backed_up = True
    except OSError:
        pass                      # a missing backup must not block the work
    if not backed_up:
        _merge_record_clear()     # slot 0 no longer describes the last merge

    _atomic_write(writer, base_path)
    if backed_up:
        _merge_record_write(base_path)


# =====================================================================
# rename_ops — pure functions, no Qt. Headlessly testable.
# =====================================================================

# ---------------------------------------------------------------------
# Windows filesystem constraints. Enforced on every platform so that a
# plan built anywhere is safe to apply on the office machines.
# ---------------------------------------------------------------------
WINDOWS_ILLEGAL_CHARS = '<>:"/\\|?*'
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"COM{i}" for i in range(1, 10)
} | {f"LPT{i}" for i in range(1, 10)}
MAX_PATH_LEN = 260
# NOT a dot-prefix. Samba's "hide dot files" sets the DOS Hidden bit on any
# dot-named file, and that bit survives the rename to the final name (Samba
# bug 14977, unfixable server-side). Explorer draws Hidden items faded, like
# a cut file - which is exactly what renamed files looked like on the share.
TEMP_PREFIX = "acrod-rn-tmp-"
_TEMP_PREFIXES = (TEMP_PREFIX,)
_FILE_ATTRIBUTE_HIDDEN = 0x2


def is_temp_name(name: str) -> bool:
    return name.startswith(_TEMP_PREFIXES)


def file_is_hidden(path: str) -> bool:
    """True when the Windows Hidden bit is set. False elsewhere / on error."""
    try:
        return bool(getattr(os.stat(path), "st_file_attributes", 0)
                    & _FILE_ATTRIBUTE_HIDDEN)
    except OSError:
        return False


def clear_hidden(path: str) -> bool:
    """Clear the Hidden bit if set. Returns True when it changed anything.

    Called only on a file whose SOURCE was not hidden: the bit was picked up
    from the temp name on the way, not chosen by the user."""
    if os.name != "nt" or not file_is_hidden(path):
        return False
    try:
        import ctypes
        attrs = os.stat(path).st_file_attributes
        ok = ctypes.windll.kernel32.SetFileAttributesW(
            path, attrs & ~_FILE_ATTRIBUTE_HIDDEN)
        return bool(ok)
    except (OSError, AttributeError):
        return False


def notify_shell_folder(folder: str) -> None:
    """Tell Explorer a folder changed so open windows repaint at once."""
    if os.name != "nt" or not folder:
        return
    try:
        import ctypes
        SHCNE_UPDATEDIR, SHCNF_PATHW, SHCNF_FLUSHNOWAIT = 0x1000, 0x0005, 0x2000
        ctypes.windll.shell32.SHChangeNotify(
            SHCNE_UPDATEDIR, SHCNF_PATHW | SHCNF_FLUSHNOWAIT,
            ctypes.c_wchar_p(os.path.abspath(folder)), None)
    except (OSError, AttributeError):
        pass


def long_paths_allowed() -> bool:
    """Opt-in only (Settings). The 260 cap is a deliberate guard: Acrobat,
    Explorer and the upload tools staff use fail on longer paths even when
    Windows itself accepts them."""
    try:
        return bool(load_ui_state().get("allow_long_paths"))
    except Exception:
        return False


def max_path_len() -> int:
    return 32000 if long_paths_allowed() else MAX_PATH_LEN


def fs_path(path: str) -> str:
    """Extended-length form for the actual filesystem call. Needed once a
    path passes MAX_PATH; UNC shares need the \\\\?\\UNC\\ spelling."""
    if os.name != "nt" or not long_paths_allowed():
        return path
    p = os.path.abspath(path)
    if len(p) < 248 or p.startswith("\\\\?\\"):
        return path
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


INCREMENT_TOKEN = "${increment}"

ST_OK = "ok"
ST_UNCHANGED = "unchanged"
ST_COLLISION = "collision"
ST_INVALID = "invalid characters"
ST_RESERVED = "reserved name"
ST_TOO_LONG = "path too long"
ST_EXISTS = "already exists"
ST_EMPTY = "empty name"
ST_EXCLUDED = "excluded"
ST_SKIPPED = "skipped"      # Auto Rename: no ID, or no keyword matched

#: What each status says to a human. "ok" is a developer's word; every other
#: status in this app was already a sentence and that one was the odd one out.
#:
#: Keyed off the ST_ CONSTANTS, never off their literal strings. Written out by
#: hand this drifted immediately - "invalid name" was typed where the constant
#: says "invalid characters", and the map silently had no entry for the status
#: it was meant to cover.
STATUS_TEXT = {
    ST_OK: "will rename",
    ST_UNCHANGED: "no change",
    ST_COLLISION: "name clash",
    ST_INVALID: "invalid characters",
    ST_RESERVED: "reserved Windows name",
    ST_TOO_LONG: "path too long",
    ST_EXISTS: "already exists",
    ST_EMPTY: "empty name",
    ST_EXCLUDED: "not selected",
    ST_SKIPPED: "skipped",
}


def plural(n: int, word: str = "file") -> str:
    """'1 file' / '2 files'. The '(s)' construction was app-wide and it is the
    kind of thing that reads as unfinished software to the person using it."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def status_text(status: str) -> str:
    return STATUS_TEXT.get(status, status)


BLOCKING_STATUSES = {
    ST_COLLISION, ST_INVALID, ST_RESERVED, ST_TOO_LONG, ST_EXISTS, ST_EMPTY
}

SCOPES = ("name", "ext", "both")
TRANSFORMS = ("none", "upper", "lower", "title")


# ---------------------------------------------------------------------
# PDF Optimizer engine
#
# Pure functions above the Qt layer, same contract as the pdf_ops block:
# no Qt imports below this comment, so the whole thing is headlessly
# testable.
#
# Goal: take a large scanned document under a hard byte ceiling while removing
# hidden information, WITHOUT destroying anything a formal document needs.
#
# Decisions that are load-bearing:
#
#   * Images are found through FORM XOBJECTS as well as directly on the page.
#     Scanner output very often wraps the page in a form; a walker that only
#     reads page.Resources.XObject reports "no images" and the file comes out
#     unchanged. That was a real defect, found by reproduction.
#
#   * Effective DPI is MEASURED from the content-stream CTM (walking q/Q/cm
#     and composing through form XObjects). If placement still cannot be
#     recovered, it falls back to the page box on the assumption the image
#     covers the page, and SAYS SO in the notes.
#
#   * Escalation ladder, measured. Each rung is applied to a fresh copy and
#     the result is weighed. Colour is dropped to grey BEFORE resolution is
#     dropped, because grey costs nothing on a black-ink document while lower
#     DPI costs legibility.
#
#   * Rungs that cannot plausibly help are SKIPPED using an extrapolation
#     from the last measured attempt. The estimate is only ever used to skip
#     work - every reported number is measured, never predicted.
#
#   * Per-image encoder bake-off on bitonal pages: CCITT G4 and Flate are
#     both encoded and the smaller kept.
#
# pikepdf is MPL-2.0 and Pillow is MIT-CMU. Ghostscript, PyMuPDF and mutool
# are all AGPL (Artifex) - unresolved for a single .exe that travels on a
# network share, so none of them is used.
# ---------------------------------------------------------------------

_PIKEPDF = None
_PIL = None


def _pikepdf():
    """Lazy, like _pypdf(). Cold start is ~85% the Qt import; most launches
    never open this tab and must not pay for it."""
    global _PIKEPDF
    if _PIKEPDF is None:
        import pikepdf
        _PIKEPDF = pikepdf
    return _PIKEPDF


def _pil():
    """Returns (Image, TiffImagePlugin, features)."""
    global _PIL
    if _PIL is None:
        from PIL import Image, TiffImagePlugin, features
        _PIL = (Image, TiffImagePlugin, features)
    return _PIL


_PDFIUM = None


def render_scale(w_pt: float, h_pt: float, wanted: float,
                 max_px: int, max_side: int) -> float:
    """The pdfium scale to use, never more than `wanted`, and never one that
    would build a bitmap over `max_px` pixels or `max_side` on a side. A page
    two points wide and 14,400 tall at thumbnail scale asked for a 2.6 GB
    bitmap (12 s, then a second copy in PIL): the PDF format allows it and a
    hostile or odd file only has to ask. The result is never below one pixel
    across the narrow side."""
    w, h = max(float(w_pt), 1e-3), max(float(h_pt), 1e-3)
    scale = float(wanted)
    if w * scale * h * scale > max_px:
        scale = (max_px / (w * h)) ** 0.5
    if max(w, h) * scale > max_side:
        scale = max_side / max(w, h)
    return max(scale, 1.0 / min(w, h))


THUMB_MAX_PX, THUMB_MAX_SIDE = 4_000_000, 4000
FLATTEN_MAX_PX, FLATTEN_MAX_SIDE = 60_000_000, 20000


_PDFIUM_LOCK = threading.Lock()   # pdfium is not thread-safe; hold per page/open


def pdfium_open_for_render(pdfium, src):
    """Open a document for DRAWING. pdfium only paints filled-in form fields
    (text boxes, check boxes, combo values) when its form environment is
    started; without it a filled form renders blank. Text-only reads do not
    need this. The caller holds _PDFIUM_LOCK."""
    doc = pdfium.PdfDocument(src)
    try:
        doc.init_forms()
    except Exception:
        pass                                    # a document without a form, or a pdfium build without it: draw as before
    return doc


def _pdfium():
    """Lazy, like the others. pypdfium2 is Apache-2.0 / BSD-3 - no AGPL
    problem - and is only imported when a page actually has to be rendered."""
    global _PDFIUM
    if _PDFIUM is None:
        import pypdfium2
        _PDFIUM = pypdfium2
    return _PDFIUM


class OptimizeCancelled(Exception):
    """Raised inside the engine when the caller's cancel check returns True."""


@dataclass
class SanitizeOptions:
    """
    Defaults are the SAFE set for a formal document.

    The five flags defaulting False are the ones that damage documents: the
    hidden text layer is what makes a scan searchable, annotations carry the
    stamps and Bates numbers, form values ARE the substance of a
    fillable form, and some recipients require bookmarked exhibits.
    """
    metadata: bool = True
    javascript: bool = True
    auto_actions: bool = True
    embedded_files: bool = True
    piece_info: bool = True
    thumbnails: bool = True
    unreferenced: bool = True

    hidden_text: bool = False
    annotations: bool = False
    reset_form_fields: bool = False
    outlines: bool = False
    links: bool = False


@dataclass
class OptimizeOptions:
    ceiling_bytes: int = int(DEFAULT_SIZE_CEILING_MB * 1_000_000)
    out_dir_name: str = OPTIMIZED_DIR_NAME
    # "smallest" (default) does what Acrobat's Optimizer does: strip hidden
    # information first, and if that is already enough, STOP. Only if it is
    # not enough does it optimize - and then it goes straight to the most
    # aggressive setting allowed rather than working down from high quality.
    # "quality" keeps the old ladder: the best-looking setting that still fits.
    mode: str = "smallest"
    allow_downsample: bool = True
    allow_gray: bool = True          # colour -> grey. Cheap and rarely harmful.
    allow_gray_to_bw: bool = False   # anything -> 1-bit. Opt in.
    # Last resort: re-render every page as a single image. Guarantees the
    # ceiling because output size stops depending on what is inside the file,
    # but it DESTROYS the searchable text layer, annotations and bookmarks.
    allow_flatten: bool = True
    flatten_dpi: int = DEFAULT_FLATTEN_DPI
    min_dpi: int = 120
    sanitize: SanitizeOptions = None
    # Operator tick-box "Flatten pages to images (force)". Re-renders every
    # page as a picture regardless of size: no ladder, no ceiling test, one
    # pass. Used when the FLATTENING is the point - killing a text layer, an
    # unwanted form or a viewer quirk - not the megabytes.
    force_flatten: bool = False
    # OPERATOR DECISION 2026-08-13: signed PDFs are optimized, not skipped.
    # Any rewrite voids a digital signature - unavoidable, and true of Acrobat
    # too - but the Optimizer never touches the original, so the only thing
    # affected is the COPY in the Optimized folder. Refusing outright left the
    # common case (a 21 MB signed file that has to meet an upload limit)
    # with no path through the tool at all. It now runs and reports the voided
    # signature on the row, in the end-of-run summary and in the saved report.
    # Set True to restore the old refuse-and-skip behaviour.
    abort_if_signed: bool = False
    overwrite: bool = False          # never clobber a previous optimized copy

    def __post_init__(self):
        if self.sanitize is None:
            self.sanitize = SanitizeOptions()


@dataclass
class ImageInfo:
    page: int
    name: str
    width: int
    height: int
    bpc: int
    colorspace: str
    filters: list
    dpi_x: float
    dpi_y: float
    placed: bool
    estimated: bool
    in_form: bool
    raw_bytes: int


@dataclass
class PdfProfile:
    path: str
    size_bytes: int
    pages: int
    signed: bool
    revisions: int
    images: list
    kind: str
    census: dict = None

    @property
    def image_bytes(self) -> int:
        return sum(i.raw_bytes for i in self.images)

    @property
    def megapixels(self) -> float:
        return sum(i.width * i.height for i in self.images) / 1_000_000.0


@dataclass
class OptimizeResult:
    src: str
    dst: str
    ok: bool
    src_bytes: int
    dst_bytes: int
    tier: str
    tier_index: int
    under_ceiling: bool
    removed: dict
    kept: dict
    notes: list
    diagnosis: str = ""
    passes: int = 0
    error: str = None
    # True when the source carried a digital signature and was optimized
    # anyway. The output is valid and readable; the signature on the COPY is
    # no longer verifiable. Never inferred from the tier or the notes - the
    # panel, the summary and the saved report all read this one field.
    signature_voided: bool = False
    # False when the size ceiling was deliberately not applied (forced
    # flatten). The panel must not report "Still too big" for a run that was
    # never trying to hit a size in the first place.
    ceiling_applied: bool = True

    @property
    def saved_pct(self) -> float:
        if not self.src_bytes:
            return 0.0
        return 100.0 * (self.src_bytes - self.dst_bytes) / self.src_bytes


def _mat_mul(a, b):
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (
        a0 * b0 + a1 * b2,
        a0 * b1 + a1 * b3,
        a2 * b0 + a3 * b2,
        a2 * b1 + a3 * b3,
        a4 * b0 + a5 * b2 + b4,
        a4 * b1 + a5 * b3 + b5,
    )


_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _walk_xobjects(container, resources, base_ctm, found, depth=0):
    """
    Walk one content stream, composing the CTM, and record every image
    placement. Descends into form XObjects (depth-limited) because scanner
    output routinely wraps the page in one.

    found: dict keyed by (objid, gen) -> {"w","h","page_local_name","in_form"}
    """
    pk = _pikepdf()
    if depth > 6 or resources is None:
        return
    try:
        ops = pk.parse_content_stream(container)
    except Exception:
        return

    xobjs = None
    try:
        xobjs = resources.get("/XObject")
    except Exception:
        xobjs = None

    ctm = base_ctm
    stack = []
    for operands, operator in ops:
        op = str(operator)
        if op == "q":
            stack.append(ctm)
        elif op == "Q":
            ctm = stack.pop() if stack else base_ctm
        elif op == "cm" and len(operands) == 6:
            try:
                ctm = _mat_mul(tuple(float(x) for x in operands), ctm)
            except Exception:
                continue
        elif op == "Do" and operands and xobjs is not None:
            name = str(operands[0])
            try:
                obj = xobjs[name]
            except Exception:
                continue
            try:
                subtype = str(obj.get("/Subtype", ""))
            except Exception:
                continue
            if subtype == "/Image":
                w = (ctm[0] ** 2 + ctm[1] ** 2) ** 0.5
                h = (ctm[2] ** 2 + ctm[3] ** 2) ** 0.5
                key = obj.objgen
                prev = found.get(key)
                if prev is None or (w * h) > (prev["w"] * prev["h"]):
                    found[key] = {"w": w, "h": h, "obj": obj,
                                  "in_form": depth > 0}
            elif subtype == "/Form":
                inner = ctm
                try:
                    matrix = obj.get("/Matrix")
                    if matrix is not None and len(matrix) == 6:
                        inner = _mat_mul(tuple(float(x) for x in matrix), ctm)
                except Exception:
                    pass
                try:
                    _walk_xobjects(obj, obj.get("/Resources"), inner, found, depth + 1)
                except Exception:
                    continue


def _page_images(page):
    """
    Every image reachable from this page, with its placement.

    Returns list of dicts: obj, w (pt), h (pt), in_form, placed.
    Images the walker could not place are still returned, with placed=False,
    so they can be handled by the page-box fallback rather than skipped.
    """
    found = {}
    try:
        _walk_xobjects(page, page.obj.get("/Resources"), _IDENTITY, found, 0)
    except Exception:
        pass

    # Safety net. If the content stream could not be parsed (or draws nothing
    # the walker recognised), sweep the resource dictionaries directly and
    # pick up any image the walk missed, unplaced. Forms are descended too,
    # because on a scanner PDF the image lives one level down and a page whose
    # operators we cannot read would otherwise look empty.
    def _sweep(resources, depth=0):
        if depth > 6 or resources is None:
            return
        try:
            xobjs = resources.get("/XObject")
        except Exception:
            return
        if xobjs is None:
            return
        for _name, obj in xobjs.items():
            try:
                subtype = str(obj.get("/Subtype", ""))
            except Exception:
                continue
            if subtype == "/Image":
                if obj.objgen not in found:
                    found[obj.objgen] = {"w": 0.0, "h": 0.0, "obj": obj,
                                         "in_form": depth > 0}
            elif subtype == "/Form":
                try:
                    _sweep(obj.get("/Resources"), depth + 1)
                except Exception:
                    continue

    try:
        _sweep(page.obj.get("/Resources"), 0)
    except Exception:
        pass

    # Annotation appearance streams. A stamp, signature or exhibit sticker
    # carries its artwork in /AP, which is NOT reachable from the page content
    # stream at all - so an image-heavy file can hide real weight there.
    try:
        for annot in page.obj.get("/Annots", []) or []:
            ap = annot.get("/AP")
            if ap is None:
                continue
            for state_key in ("/N", "/D", "/R"):
                node = ap.get(state_key)
                if node is None:
                    continue
                candidates = [node]
                try:
                    if not hasattr(node, "read_raw_bytes"):
                        candidates = list(node.values())
                except Exception:
                    candidates = [node]
                for cand in candidates:
                    try:
                        if str(cand.get("/Subtype", "")) == "/Image":
                            if cand.objgen not in found:
                                found[cand.objgen] = {"w": 0.0, "h": 0.0,
                                                      "obj": cand, "in_form": True}
                        else:
                            _sweep(cand.get("/Resources"), 1)
                    except Exception:
                        continue
    except Exception:
        pass

    out = []
    for key, rec in found.items():
        rec = dict(rec)
        rec["placed"] = rec["w"] > 0 and rec["h"] > 0
        rec["key"] = key
        out.append(rec)
    return out


def _page_box(page):
    """Page width/height in points, from CropBox if present else MediaBox."""
    for key in ("/CropBox", "/MediaBox"):
        try:
            box = page.obj.get(key)
            if box is not None and len(box) == 4:
                x0, y0, x1, y1 = (float(v) for v in box)
                return abs(x1 - x0), abs(y1 - y0)
        except Exception:
            continue
    return 612.0, 792.0


def _target_pixels(rec, page, target_dpi):
    """
    Target pixel size for an image at `target_dpi`, computed from GEOMETRY
    rather than from a measured-DPI ratio.

    This exists because the ratio approach silently does nothing when the
    measured DPI comes out wrong: `if measured > target` is simply False and
    the image is left at full resolution, which reads as "resampled to 120
    DPI" in the report while the pixels never changed. Page and placement
    geometry are reliable; a derived DPI is not.

    Placement is used when it is present AND sane. A placement wider than
    1.5x the page is treated as untrustworthy and the page box wins.
    """
    obj = rec["obj"]
    try:
        w_px, h_px = int(obj.Width), int(obj.Height)
    except Exception:
        return None
    page_w_pt, page_h_pt = _page_box(page)

    w_pt = rec["w"] if rec.get("placed") else 0.0
    h_pt = rec["h"] if rec.get("placed") else 0.0
    if not (w_pt > 1.0 and h_pt > 1.0) or w_pt > page_w_pt * 1.5 or h_pt > page_h_pt * 1.5:
        w_pt, h_pt = page_w_pt, page_h_pt

    tw = max(1, int(round((w_pt / 72.0) * target_dpi)))
    th = max(1, int(round((h_pt / 72.0) * target_dpi)))
    if tw >= w_px and th >= h_px:
        return None                      # already at or below target
    # Preserve the image's own aspect ratio; drive off whichever axis needs
    # the larger reduction so neither dimension stays above target.
    scale = min(tw / w_px, th / h_px)
    return max(1, int(w_px * scale)), max(1, int(h_px * scale))


def _effective_dpi(rec, page):
    """
    (dpi, estimated). Uses the measured placement when available; otherwise
    falls back to the page box on the assumption the image covers the page,
    which is true of essentially every scan. The caller is told which it got.
    """
    obj = rec["obj"]
    try:
        w_px = int(obj.Width)
    except Exception:
        return 0.0, False
    if rec["placed"] and rec["w"] > 1.0:
        return w_px / (rec["w"] / 72.0), False
    pw, _ph = _page_box(page)
    if pw > 1.0:
        return w_px / (pw / 72.0), True
    return 0.0, False


def _count_revisions(path: str) -> int:
    with open(path, "rb") as fh:
        return fh.read().count(b"%%EOF")


def _signature_state(pdf) -> str:
    """
    Which of the three different things called "a signature" this file has.

    "signed"      at least one signature field carries an APPLIED cryptographic
                  signature - a /V holding a /ByteRange. Rewriting voids it.
    "placeholder" signature fields exist but NONE has been signed. An empty
                  "Signature" box. Nothing to void.
    "none"        no signature fields at all.

    FIXED 2026-08-14. The old _is_signed() returned True for BOTH of the first
    two, because it tested /SigFlags or the mere presence of an /FT /Sig field.
    /SigFlags=1 only means "this document contains signature fields" - it is
    set on blank forms - so every such form was reported as signed and
    carried a false SIGNATURE VOIDED warning. Reproduced on a known-answer
    case: an empty placeholder and an applied signature were indistinguishable.

    NOTE what this does NOT cover: an ink signature scanned or drawn onto the
    page is just pixels. It is not a signature field, it is reported "none",
    and no PDF operation can void it - it survives everything, including
    flattening. That is the kind a reader SEES, which is exactly why seeing a
    signature in the output says nothing about whether a cryptographic one
    survived.
    """
    def walk(fields, depth=0):
        # Signature fields are frequently /Kids of a parent field, so a
        # top-level-only scan misses them.
        if depth > 8:
            return
        for field in fields or []:
            try:
                if str(field.get("/FT", "")) == "/Sig":
                    yield field
                kids = field.get("/Kids")
                if kids is not None:
                    yield from walk(kids, depth + 1)
            except Exception:
                continue

    try:
        acro = pdf.Root.get("/AcroForm")
        if acro is None:
            return "none"
        seen_field = False
        for field in walk(acro.get("/Fields", [])):
            seen_field = True
            v = field.get("/V")
            if v is None:
                continue
            # An applied signature has a /ByteRange - that is the span of the
            # file the digest covers, and it is what a rewrite invalidates.
            # A /V without one is not something a rewrite can void.
            try:
                if v.get("/ByteRange") is not None:
                    return "signed"
            except Exception:
                continue
        if seen_field or acro.get("/SigFlags"):
            return "placeholder"
    except Exception:
        pass
    return "none"


def _is_signed(pdf) -> bool:
    """True only when a signature is APPLIED - see _signature_state."""
    return _signature_state(pdf) == "signed"


def profile_pdf(path: str, data: bytes = None) -> PdfProfile:
    """Read-only inspection. Raises PdfOpError in plain language.

    `data` is the file's bytes when the caller already holds them; the path
    is then used only for naming. Saves two full reads over the wire per
    optimize run."""
    if data is None:
        require_safe_to_expand(path)
    else:
        problem = _scan_for_bombs(data, os.path.basename(path))
        if problem:
            raise PdfOpError(problem)
    pk = _pikepdf()
    size = len(data) if data is not None else os.path.getsize(path)
    try:
        pdf = pk.open(io.BytesIO(data) if data is not None else path)
    except pk.PasswordError:
        raise PdfOpError(
            f"{os.path.basename(path)} is password protected. "
            "Remove the password and try again."
        )
    except Exception as exc:
        raise PdfOpError(
            f"{os.path.basename(path)} could not be read as a PDF ({exc})."
        )

    images = []
    seen = set()
    with pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            for rec in _page_images(page):
                if rec["key"] in seen:
                    continue
                seen.add(rec["key"])
                obj = rec["obj"]
                try:
                    w = int(obj.Width)
                    h = int(obj.Height)
                    bpc = int(obj.get("/BitsPerComponent", 8))
                    cs = str(obj.get("/ColorSpace", "?"))
                    filt = obj.get("/Filter")
                    filters = (
                        [str(f) for f in filt] if isinstance(filt, pk.Array)
                        else ([str(filt)] if filt is not None else [])
                    )
                    dpi, estimated = _effective_dpi(rec, page)
                    images.append(ImageInfo(
                        page=pno, name="", width=w, height=h, bpc=bpc,
                        colorspace=cs, filters=filters,
                        dpi_x=dpi, dpi_y=dpi,
                        placed=rec["placed"], estimated=estimated,
                        in_form=rec["in_form"],
                        raw_bytes=len(obj.read_raw_bytes()),
                    ))
                except (TypeError, AttributeError):
                    # A programming error must not be swallowed into "this PDF
                    # has no images" - that is how a scan gets classified as
                    # vector and skips every image tier.
                    raise
                except Exception:
                    continue
        npages = len(pdf.pages)
        signed = _is_signed(pdf)
        try:
            census = byte_census(pdf)
        except Exception:
            census = {}

    if not images:
        kind = "vector"
    else:
        bits = {1 if im.bpc == 1 else (8 if "Gray" in im.colorspace else 24)
                for im in images}
        kind = ({1: "bitonal", 8: "gray", 24: "colour"}.get(next(iter(bits)))
                if len(bits) == 1 else "mixed")

    revisions = (data.count(b"%%EOF") if data is not None
                 else _count_revisions(path))
    return PdfProfile(path=path, size_bytes=size, pages=npages, signed=signed,
                      revisions=revisions, images=images, kind=kind,
                      census=census)


def describe_profile(prof: PdfProfile) -> str:
    """
    Plain-language explanation of what is taking up the space. This is what
    answers 'why will it not get smaller' without anyone reading code.
    """
    lines = [
        f"{plural(prof.pages, 'page')}, {prof.size_bytes / 1_000_000:.2f} MB "
        f"({prof.size_bytes / max(prof.pages, 1) / 1_000_000:.2f} MB per page)."
    ]
    if not prof.images:
        lines.append(
            "No page images were found, so this is a text/vector PDF. There is "
            "very little to compress - the size is in fonts and drawing "
            "instructions, not scans."
        )
        return "\n".join(lines)

    share = 100.0 * prof.image_bytes / max(prof.size_bytes, 1)
    lines.append(
        f"{plural(len(prof.images), 'image')}, {prof.megapixels:.1f} megapixels total, "
        f"{prof.image_bytes / 1_000_000:.2f} MB of image data "
        f"({share:.0f}% of the file). Colour type: {prof.kind}."
    )
    dpis = [i.dpi_x for i in prof.images if i.dpi_x > 1]
    if dpis:
        big = [i.dpi_x for i in prof.images
               if i.dpi_x > 1 and i.width * i.height > 250_000]
        span = big or dpis
        lines.append(
            f"Full-page scans are about {min(span):.0f}-{max(span):.0f} DPI."
            + (f" ({plural(len(dpis) - len(big), 'small logo/stamp image')} excluded.)"
               if big and len(big) < len(dpis) else "")
        )
    unknown = [i for i in prof.images if i.dpi_x <= 1]
    if unknown:
        lines.append(f"{plural(len(unknown), 'image')} had no usable resolution reading.")
    if prof.census:
        lines.append("Where the bytes are:")
        for name, n in sorted(prof.census.items(), key=lambda kv: -kv[1]):
            lines.append(f"   {name}: {n / 1_000_000:.2f} MB "
                         f"({100.0 * n / max(prof.size_bytes, 1):.0f}%)")
        accounted = sum(prof.census.values())
        rest = prof.size_bytes - accounted
        if rest > prof.size_bytes * 0.05:
            # These bytes are NOT live content. On a file with an edit history
            # they are the earlier saved versions, which the rewrite discards
            # for free - charging them to "weight that cannot be compressed"
            # is how a file that only needed its scans shrunk gets flattened.
            label = ("Earlier saved versions and file structure"
                     if prof.revisions > 1 else "File structure and tables")
            lines.append(f"   {label}: {rest / 1_000_000:.2f} MB "
                         f"({100.0 * rest / max(prof.size_bytes, 1):.0f}%)"
                         + (" - discarded on save" if prof.revisions > 1 else ""))
        non_image = accounted - prof.census.get("Scanned images", 0)
        lines.append(
            f"   NOTE: {non_image / 1_000_000:.2f} MB of live content is NOT "
            "image data. Compressing the scans can never get the file below "
            "that figure - re-rendering the pages as images is the only thing "
            "that removes it."
        )
    n_est = sum(1 for i in prof.images if i.estimated)
    n_form = sum(1 for i in prof.images if i.in_form)
    if n_form:
        lines.append(f"{plural(n_form, 'image')} sit inside a form container.")
    if n_est:
        lines.append(
            f"{plural(n_est, 'image')} had no readable placement, so their resolution "
            "was estimated from the page size."
        )
    return "\n".join(lines)


def byte_census(pdf) -> dict:
    """
    Where every compressed byte in the file actually lives.

    Written because a 30 MB scan reported 51% image data and shrinking the
    images to nothing still left ~15 MB unaccounted for. Guessing at that
    remainder twice would have been worse than measuring it once.
    """
    census = {}
    counted = set()

    def add(bucket, obj):
        try:
            key = obj.objgen
        except Exception:
            return
        if key in counted or key == (0, 0):
            return
        try:
            n = len(obj.read_raw_bytes())
        except Exception:
            return
        counted.add(key)
        census[bucket] = census.get(bucket, 0) + n

    pk = _pikepdf()

    # Page content streams first. Classified last, they were being swallowed
    # by the generic "Other streams" bucket and reported as 0.00 MB - an
    # instrument that quietly hid one of the two things it exists to find.
    for page in pdf.pages:
        try:
            contents = page.obj.get("/Contents")
        except Exception:
            continue
        if contents is None:
            continue
        items = contents if isinstance(contents, pk.Array) else [contents]
        for c in items:
            add("Page text and drawing commands", c)

    for obj in pdf.objects:
        try:
            if not isinstance(obj, pk.Stream):
                continue
            subtype = str(obj.get("/Subtype", ""))
            otype = str(obj.get("/Type", ""))
        except Exception:
            continue
        if subtype == "/Image":
            add("Scanned images", obj)
        elif subtype == "/Form":
            add("Drawing instructions (form containers)", obj)
        elif otype == "/Metadata":
            add("Metadata", obj)
        elif subtype in ("/Type1C", "/CIDFontType0C", "/OpenType") or otype == "/Font":
            add("Embedded fonts", obj)
        else:
            add("Other streams", obj)

    # Fonts hide behind FontFile keys rather than a Subtype on the stream.
    # (Run after the generic sweep and move any that were mis-bucketed.)
    for obj in pdf.objects:
        try:
            for key in ("/FontFile", "/FontFile2", "/FontFile3"):
                ff = obj.get(key) if hasattr(obj, "get") else None
                if ff is not None:
                    try:
                        if ff.objgen in counted:
                            census["Other streams"] = census.get("Other streams", 0) - len(ff.read_raw_bytes())
                            counted.discard(ff.objgen)
                    except Exception:
                        pass
                    add("Embedded fonts", ff)
        except Exception:
            continue

    return {k: v for k, v in census.items() if v > 0}


def _bump(manifest: dict, key: str, n: int = 1):
    if n:
        manifest[key] = manifest.get(key, 0) + n


_ACTIVE_ACTIONS = frozenset((
    "/JavaScript", "/Launch", "/GoToR", "/GoToE", "/SubmitForm", "/ImportData",
    "/Rendition", "/Movie", "/Sound", "/GoTo3DView", "/RichMediaExecute"))
_ACTIVE_ANNOTS = frozenset(("/Screen", "/RichMedia", "/Movie", "/Sound", "/3D"))


def _action_is_active(act, depth=0):
    """True if an action dictionary, or anything chained after it through /Next,
    can run code, start a program, open another file or send form data. Plain
    links (/URI), page jumps (/GoTo), /Named and /ResetForm stay."""
    if act is None or depth > 32:
        return False
    try:
        if str(act.get("/S", "")) in _ACTIVE_ACTIONS:
            return True
        nxt = act.get("/Next")
        if nxt is None:
            return False
        if hasattr(nxt, "keys"):
            return _action_is_active(nxt, depth + 1)
        return any(_action_is_active(n, depth + 1) for n in nxt)
    except Exception:
        return True                      # cannot read it: treat as active


def _strip_active_holder(obj, removed):
    """Remove an active /A and every /AA from one dictionary (annotation,
    form field, bookmark). Returns how many things were removed."""
    n = 0
    try:
        if "/A" in obj and _action_is_active(obj["/A"]):
            del obj["/A"]
            _bump(removed, "Annotation JavaScript")
            n += 1
        if "/AA" in obj:
            del obj["/AA"]
            _bump(removed, "Automatic actions")
            n += 1
    except Exception:
        pass
    return n


def _walk_tree(node, visit, seen=None, depth=0, key="/Kids"):
    """Visit every dictionary reachable through `key` (cycle- and depth-safe)."""
    if seen is None:
        seen = set()
    if node is None or depth > 200:
        return
    try:
        og = node.objgen
        if og != (0, 0):
            if og in seen:
                return
            seen.add(og)
        visit(node)
        kids = node.get(key)
        if kids is not None:
            for k in kids:
                _walk_tree(k, visit, seen, depth + 1, key)
    except Exception:
        return


def sanitize_pdf(pdf, opt: SanitizeOptions):
    """Mutates pdf in place. Returns (removed, kept) manifests."""
    pk = _pikepdf()
    removed, kept = {}, {}
    root = pdf.Root

    if opt.metadata:
        if "/Metadata" in root:
            del root["/Metadata"]
            _bump(removed, "Hidden metadata")
        try:
            if len(pdf.docinfo):
                _bump(removed, "Document properties")
            del pdf.docinfo
        except Exception:
            pass
        for page in pdf.pages:
            if "/Metadata" in page.obj:
                del page.obj["/Metadata"]
                _bump(removed, "Page metadata")

    names = root.get("/Names")
    if opt.javascript and names is not None and "/JavaScript" in names:
        del names["/JavaScript"]
        _bump(removed, "Document JavaScript")

    if opt.embedded_files and names is not None and "/EmbeddedFiles" in names:
        try:
            n = len(names["/EmbeddedFiles"].get("/Names", [])) // 2
        except Exception:
            n = 1
        del names["/EmbeddedFiles"]
        _bump(removed, "Embedded files", max(n, 1))

    if opt.auto_actions:
        for key in ("/OpenAction", "/AA"):
            if key in root:
                del root[key]
                _bump(removed, "Automatic actions")
        for page in pdf.pages:
            if "/AA" in page.obj:
                del page.obj["/AA"]
                _bump(removed, "Automatic actions")

    if opt.piece_info:
        for key in ("/PieceInfo", "/Legacy"):
            if key in root:
                del root[key]
                _bump(removed, "Application private data")
        for page in pdf.pages:
            if "/PieceInfo" in page.obj:
                del page.obj["/PieceInfo"]
                _bump(removed, "Application private data")

    if opt.thumbnails:
        for page in pdf.pages:
            if "/Thumb" in page.obj:
                del page.obj["/Thumb"]
                _bump(removed, "Page thumbnails")

    if opt.outlines and "/Outlines" in root:
        del root["/Outlines"]
        _bump(removed, "Bookmarks")
    elif "/Outlines" in root:
        kept["Bookmarks"] = 1

    # Annotations are walked either way: even when a stamp is KEPT, any
    # JavaScript or auto-action hanging off it is still stripped.
    n_annots = n_sig = 0
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if annots is None:
            continue
        survivors = []
        for a in annots:
            try:
                sub = str(a.get("/Subtype", ""))
            except Exception:
                survivors.append(a)
                continue
            if sub == "/Widget" and str(a.get("/FT", "")) == "/Sig":
                n_sig += 1
            drop = False
            if opt.annotations and sub not in ("/Widget", "/Link"):
                drop = True
            if opt.links and sub == "/Link":
                drop = True
            if drop:
                _bump(removed, "Comments and markups")
                continue
            if opt.javascript:
                if sub in _ACTIVE_ANNOTS:
                    _bump(removed, "Comments and markups")
                    continue
                if "/A" in a and _action_is_active(a["/A"]):
                    del a["/A"]
                    _bump(removed, "Annotation JavaScript")
                if "/AA" in a and opt.auto_actions:
                    del a["/AA"]
                    _bump(removed, "Automatic actions")
            if opt.embedded_files and sub == "/FileAttachment":
                fs = a.get("/FS")
                if fs is not None and "/EF" in fs:
                    del fs["/EF"]
                    _bump(removed, "Embedded files")
            if opt.reset_form_fields and sub == "/Widget":
                for k in ("/V", "/AS"):
                    if k in a:
                        del a[k]
                _bump(removed, "Form field values")
            survivors.append(a)
            n_annots += 1
        page.obj["/Annots"] = pdf.make_indirect(pk.Array(survivors))

    if opt.javascript:
        # Scripts and programs also hide where no page points: the form-field
        # tree (orphan fields still run their calculate/format scripts), the
        # XFA form packet, and bookmark actions when bookmarks are kept.
        af = root.get("/AcroForm")
        if af is not None:
            try:
                if "/XFA" in af:
                    del af["/XFA"]
                    _bump(removed, "Document JavaScript")
                if "/AA" in af:
                    del af["/AA"]
                    _bump(removed, "Automatic actions")
                for fld in af.get("/Fields", []):
                    _walk_tree(fld, lambda f: _strip_active_holder(f, removed))
            except Exception:
                pass
        if "/Outlines" in root and not opt.outlines:
            try:
                seen_o = set()
                def _walk_outline(node, depth=0):
                    while node is not None and depth < 200:
                        og = node.objgen
                        if og != (0, 0):
                            if og in seen_o:
                                return
                            seen_o.add(og)
                        _strip_active_holder(node, removed)
                        if node.get("/First") is not None:
                            _walk_outline(node["/First"], depth + 1)
                        node = node.get("/Next")
                _walk_outline(root["/Outlines"].get("/First"))
            except Exception:
                pass
    if opt.embedded_files:
        for holder in [root] + [pg.obj for pg in pdf.pages]:
            try:
                if "/AF" in holder:
                    del holder["/AF"]
                    _bump(removed, "Embedded files")
            except Exception:
                pass

    if n_annots:
        kept["Stamps, annotations and links"] = n_annots
    if n_sig:
        kept["Signature fields"] = n_sig
    if not opt.hidden_text:
        kept["Searchable (hidden) text layer"] = 1

    if opt.unreferenced:
        try:
            pdf.remove_unreferenced_resources()
        except Exception:
            pass

    return removed, kept


def _g4_encode(pil1):
    """
    1-bit PIL image -> raw CCITT Group 4 strip, or None.

    STRIP_SIZE must be raised or Pillow emits multiple strips; G4 strips are
    independently coded, so concatenating them decodes to garbage. VERIFIED:
    single strip + BlackIs1=True round-trips pixel-identical through pikepdf.
    """
    Image, Tiff, features = _pil()
    if not features.check("libtiff"):
        return None
    old = Tiff.STRIP_SIZE
    Tiff.STRIP_SIZE = 1 << 31
    try:
        buf = io.BytesIO()
        pil1.save(buf, format="TIFF", compression="group4")
    except Exception:
        return None
    finally:
        Tiff.STRIP_SIZE = old
    buf.seek(0)
    try:
        tif = Image.open(buf)
        offs = tif.tag_v2[Tiff.STRIPOFFSETS]
        cnts = tif.tag_v2[Tiff.STRIPBYTECOUNTS]
        if len(offs) != 1:
            return None
        return buf.getvalue()[offs[0]:offs[0] + cnts[0]]
    except Exception:
        return None


def _write_bilevel(obj, pil1) -> bool:
    """Encoder bake-off: G4 vs Flate, smallest wins. True if written."""
    pk = _pikepdf()
    w, h = pil1.size
    flate = zlib.compress(pil1.tobytes(), 9)
    g4 = _g4_encode(pil1)

    if g4 is not None and len(g4) < len(flate):
        data = g4
        filt = pk.Name.CCITTFaxDecode
        parms = pk.Dictionary(K=-1, Columns=w, Rows=h, BlackIs1=True)
    else:
        data, filt, parms = flate, pk.Name.FlateDecode, None

    if len(data) >= len(obj.read_raw_bytes()):
        return False
    obj.write(data, filter=filt, decode_parms=parms)
    obj.Width, obj.Height = w, h
    obj.BitsPerComponent = 1
    obj.ColorSpace = pk.Name.DeviceGray
    for key in ("/SMask", "/Mask", "/Decode", "/Interpolate"):
        if key in obj:
            del obj[key]
    return True


def _write_jpeg(obj, pilimg, quality: int, force_gray: bool) -> bool:
    pk = _pikepdf()
    buf = io.BytesIO()
    mode = "L" if (force_gray or pilimg.mode in ("L", "1")) else "RGB"
    pilimg = pilimg.convert(mode)
    # 4:2:0 chroma subsampling on colour. Invisible on document scans and a
    # substantial saving; ignored for greyscale.
    pilimg.save(buf, "JPEG", quality=quality, optimize=True,
                progressive=False, subsampling=2 if mode == "RGB" else 0)
    data = buf.getvalue()
    if len(data) >= len(obj.read_raw_bytes()):
        return False
    obj.write(data, filter=pk.Name.DCTDecode)
    obj.Width, obj.Height = pilimg.size
    obj.BitsPerComponent = 8
    obj.ColorSpace = pk.Name.DeviceGray if mode == "L" else pk.Name.DeviceRGB
    for key in ("/Decode", "/Interpolate", "/SMask"):
        if key in obj:
            del obj[key]
    return True


def _otsu(pil_l) -> int:
    """Otsu threshold - beats a fixed 128 cut on faint signatures and seals."""
    hist = pil_l.histogram()[:256]
    total = sum(hist) or 1
    sum_all = sum(i * hist[i] for i in range(256))
    sum_b = wb = 0.0
    best_t, best_var = 128, -1.0
    for t in range(256):
        wb += hist[t]
        if wb == 0:
            continue
        wf = total - wb
        if wf == 0:
            break
        sum_b += t * hist[t]
        mb = sum_b / wb
        mf = (sum_all - sum_b) / wf
        var = wb * wf * (mb - mf) ** 2
        if var > best_var:
            best_var, best_t = var, t
    return best_t


def _image_data_matches_its_size(obj, w: int, h: int) -> bool:
    """False when a Flate image inflates to far more than its own Width x Height x depth allows.
    The pre-flight cannot inflate every scanned page, but this step is about to decode the image
    anyway, so it checks first, stopping one 1 MB step past the allowed size."""
    try:
        filt = obj.get("/Filter")
        names = [str(f) for f in filt] if isinstance(filt, _pikepdf().Array) else [str(filt)]
        if names != ["/FlateDecode"]:
            return True
        bpc = int(obj.get("/BitsPerComponent", 8))
        allowed = int(w * h * 4 * max(bpc, 8) // 8 * 1.05) + 65536
        if allowed > BOMB_IMAGE_MAX:
            return False
        return _inflate_capped(obj.read_raw_bytes(), 0, allowed) <= allowed
    except Exception:
        return True                            # undecidable: let the existing decode path judge it


def _recompress_images(pdf, tier: dict, notes: list, on_page=None,
                       should_cancel=None) -> int:
    Image, _tiff, _f = _pil()
    changed = 0
    seen = set()
    skipped = {}
    resized = [0]
    total_pages = len(pdf.pages)

    for pno, page in enumerate(pdf.pages, start=1):
        if should_cancel is not None and should_cancel():
            raise OptimizeCancelled()
        if on_page is not None:
            on_page(pno, total_pages)

        for rec in _page_images(page):
            if rec["key"] in seen:
                continue
            seen.add(rec["key"])
            obj = rec["obj"]

            try:
                w, h = int(obj.Width), int(obj.Height)
            except Exception:
                continue
            if w * h < 10_000:
                continue                      # icons and logos are not the bytes
            if "/SMask" in obj or "/Mask" in obj:
                skipped["has transparency"] = skipped.get("has transparency", 0) + 1
                continue

            if not _image_data_matches_its_size(obj, w, h):
                skipped["has more data than its size allows"] = skipped.get("has more data than its size allows", 0) + 1
                continue

            try:
                pilimg = _pikepdf().PdfImage(obj).as_pil_image()
            except Exception:
                skipped["could not be decoded"] = skipped.get("could not be decoded", 0) + 1
                continue

            target_dpi = tier.get("dpi")
            work = pilimg
            if target_dpi:
                target = _target_pixels(rec, page, target_dpi)
                if target is not None:
                    work = pilimg.resize(target, Image.LANCZOS)
                    resized[0] += 1
                else:
                    skipped["already at or below the target resolution"] = \
                        skipped.get("already at or below the target resolution", 0) + 1

            is_bilevel = pilimg.mode == "1"
            if tier.get("to_bw") and not is_bilevel:
                gray = work.convert("L")
                thr = _otsu(gray)
                work = gray.point(lambda v, t=thr: 255 if v > t else 0).convert("1")
                is_bilevel = True
            elif is_bilevel:
                work = work.convert("1", dither=Image.Dither.NONE)

            try:
                if is_bilevel:
                    ok = _write_bilevel(obj, work)
                elif tier.get("quality"):
                    ok = _write_jpeg(obj, work, tier["quality"],
                                     force_gray=bool(tier.get("gray")))
                else:
                    ok = False
            except Exception:
                skipped["could not be re-encoded"] = skipped.get("could not be re-encoded", 0) + 1
                ok = False
            if ok:
                changed += 1

    for reason, count in sorted(skipped.items()):
        notes.append(f"{plural(count, 'image')} left as-is: {reason}.")
    if tier.get("dpi"):
        notes.append(f"{plural(resized[0], 'image')} actually resampled to {tier['dpi']} DPI.")
    return changed


# The ladder. Colour is dropped to grey BEFORE resolution is dropped, because
# grey costs nothing on a black-ink document while lower DPI costs legibility.
# 'factor' is a ROUGH size multiplier relative to full-colour at the same DPI,
# used ONLY to skip rungs that cannot plausibly reach the ceiling. It is never
# reported - every number the user sees is measured.
OPTIMIZE_TIERS = [
    {"i": 0, "name": "Cleaned only", "images": False, "factor": 1.0},
    {"i": 1, "name": "Repacked scans", "images": True, "factor": 1.0},
    {"i": 2, "name": "300 DPI", "images": True, "dpi": 300, "quality": 85, "factor": 1.0},
    {"i": 3, "name": "300 DPI greyscale", "images": True, "dpi": 300, "quality": 80, "gray": True, "factor": 0.5},
    {"i": 4, "name": "200 DPI greyscale", "images": True, "dpi": 200, "quality": 75, "gray": True, "factor": 0.5},
    {"i": 5, "name": "150 DPI greyscale", "images": True, "dpi": 150, "quality": 65, "gray": True, "factor": 0.5},
    {"i": 6, "name": "120 DPI greyscale", "images": True, "dpi": 120, "quality": 60, "gray": True, "factor": 0.5},
    {"i": 7, "name": "300 DPI black & white", "images": True, "dpi": 300, "to_bw": True, "factor": 0.12},
    {"i": 8, "name": "200 DPI black & white", "images": True, "dpi": 200, "to_bw": True, "factor": 0.12},
    {"i": 9, "name": "150 DPI black & white", "images": True, "dpi": 150, "to_bw": True, "factor": 0.12},
]


def flatten_to_images(source, dpi=120, quality=50, to_bw=False,
                      on_page=None, should_cancel=None) -> bytes:
    """
    Re-render every page and rebuild the document as one image per page.

    This is the only operation that GUARANTEES a size, because the output
    stops depending on what was inside the original - it becomes a function
    of page count, DPI and encoder alone.

    It is destructive and must never be a default: the searchable text layer,
    annotations, form fields, bookmarks and links do not survive. It is the
    equivalent of printing to a scanner.
    """
    require_safe_to_expand(source)
    pk = _pikepdf()
    Image, _tiff, _f = _pil()
    pdfium = _pdfium()

    with _PDFIUM_LOCK:
        doc = pdfium_open_for_render(pdfium, source)
    out = pk.new()
    total = len(doc)
    try:
        for index in range(total):
            if should_cancel is not None and should_cancel():
                raise OptimizeCancelled()
            if on_page is not None:
                on_page(index + 1, total)

            with _PDFIUM_LOCK:
                page = doc[index]
                try:
                    width_pt, height_pt = page.get_size()
                    _w, _h = page.get_size()
                    bitmap = page.render(scale=render_scale(
                        _w, _h, dpi / 72.0, FLATTEN_MAX_PX, FLATTEN_MAX_SIDE))
                    pil = bitmap.to_pil()
                finally:
                    # Explicit, so a 200-page render does not accumulate native
                    # page handles for the whole run.
                    for handle in (locals().get("bitmap"), page):
                        try:
                            handle.close()
                        except Exception:
                            pass

            if to_bw:
                gray = pil.convert("L")
                thr = _otsu(gray)
                bilevel = gray.point(lambda v, t=thr: 255 if v > t else 0).convert("1")
                data = _g4_encode(bilevel)
                if data is not None:
                    filt = pk.Name.CCITTFaxDecode
                    parms = pk.Dictionary(K=-1, Columns=bilevel.width,
                                          Rows=bilevel.height, BlackIs1=True)
                else:
                    data = zlib.compress(bilevel.tobytes(), 9)
                    filt, parms = pk.Name.FlateDecode, None
                w_px, h_px = bilevel.size
                bpc, cspace = 1, pk.Name.DeviceGray
            else:
                gray = pil.convert("L")
                buf = io.BytesIO()
                gray.save(buf, "JPEG", quality=quality, optimize=True)
                data, filt, parms = buf.getvalue(), pk.Name.DCTDecode, None
                w_px, h_px = gray.size
                bpc, cspace = 8, pk.Name.DeviceGray

            stream = pk.Stream(out, data)
            stream.stream_dict = pk.Dictionary(
                Type=pk.Name.XObject, Subtype=pk.Name.Image,
                Width=w_px, Height=h_px, ColorSpace=cspace,
                BitsPerComponent=bpc, Filter=filt,
            )
            if parms is not None:
                stream.stream_dict.DecodeParms = parms
            xobj = out.make_indirect(stream)

            content = (f"q {width_pt:.4f} 0 0 {height_pt:.4f} 0 0 cm /Im0 Do Q"
                       ).encode("ascii")
            page_obj = out.make_indirect(pk.Dictionary(
                Type=pk.Name.Page,
                MediaBox=[0, 0, width_pt, height_pt],
                Resources=pk.Dictionary(XObject=pk.Dictionary(Im0=xobj)),
                Contents=out.make_indirect(pk.Stream(out, content)),
            ))
            out.pages.append(pk.Page(page_obj))
    finally:
        try:
            doc.close()
        except Exception:
            pass

    return _save_pdf_bytes(out)


def _save_pdf_bytes(pdf) -> bytes:
    pk = _pikepdf()
    buf = io.BytesIO()
    pdf.save(
        buf,
        compress_streams=True,
        recompress_flate=True,
        object_stream_mode=pk.ObjectStreamMode.generate,
        linearize=False,
        deterministic_id=True,
    )
    return buf.getvalue()


def _unique_destination(dest_path: str) -> str:
    """
    Windows-style 'name (1).pdf' when the destination already exists.

    An optimized copy is evidence of what was done to an important document. A
    second run at a different limit used to overwrite the first with no
    warning and no record - verified by hashing the output before and after.
    """
    if not os.path.exists(dest_path):
        return dest_path
    stem, ext = os.path.splitext(dest_path)
    for n in range(1, 1000):
        candidate = f"{stem} ({n}){ext}"
        if not os.path.exists(candidate):
            return candidate
    return dest_path


def _write_bytes_atomic(dest_path: str, data: bytes) -> None:
    """Same guarantee as _atomic_write, for raw bytes rather than a writer."""
    tmp = dest_path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest_path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def inspect_pdf(path: str) -> dict:
    """Read-only report on one PDF: what is in it and what the Optimizer's
    hidden-information pass WOULD remove. Writes nothing and changes nothing;
    the cleanup runs on an in-memory copy that is never saved.

    Raises PdfOpError in plain language (password, damaged)."""
    prof = profile_pdf(path)
    pk = _pikepdf()
    try:
        pdf = pk.open(path)
    except pk.PasswordError:
        raise PdfOpError(f"{os.path.basename(path)} is password protected. "
                         "Remove the password and try again.")
    except Exception:
        raise PdfOpError(f"{os.path.basename(path)} could not be opened. "
                         "It may be damaged.")
    try:
        would_remove, kept = sanitize_pdf(pdf, SanitizeOptions())
    finally:
        pdf.close()
    return {
        "path": path, "name": os.path.basename(path),
        "size_bytes": prof.size_bytes, "pages": prof.pages,
        "signed": prof.signed, "revisions": prof.revisions, "kind": prof.kind,
        "image_bytes": prof.image_bytes, "megapixels": prof.megapixels,
        "would_remove": dict(would_remove), "kept": sorted(kept),
        "census": dict(prof.census or {}),
        "text": describe_profile(prof),
    }


def format_inspect_report(rep: dict) -> str:
    """Plain-language text for one inspect_pdf() result."""
    lines = [rep["name"], rep["path"], "", "WHAT IS IN THIS FILE:"]
    lines += [f"   {ln}" for ln in rep["text"].splitlines()]
    flags = []
    if rep["signed"]:
        flags.append("It carries a digital signature. Optimizing it would void "
                     "the signature on the copy.")
    if rep["revisions"] > 1:
        flags.append(f"It has {rep['revisions']} saved revisions; the older ones "
                     "are discarded when the Optimizer writes a copy.")
    if flags:
        lines += ["", "WORTH KNOWING:"] + [f"   • {f}" for f in flags]
    lines += ["", "THE OPTIMIZER WOULD REMOVE:"]
    lines += ([f"   • {k}: {v}" for k, v in sorted(rep["would_remove"].items())]
              or ["   • nothing hidden was found in this file"])
    lines += ["", "AND WOULD KEEP ON PURPOSE:"]
    lines += ([f"   • {k}" for k in rep["kept"]] or ["   • nothing"])
    lines += ["", "Nothing was written or changed."]
    return "\n".join(lines)


def optimize_pdf(src_path: str, opt: OptimizeOptions = None,
                 on_progress=None, should_cancel=None) -> OptimizeResult:
    """
    Optimize one PDF. The source is left INTACT; output goes to a subfolder
    beside it.

    on_progress(stage_text, done, total) is called often enough to drive a
    progress bar; should_cancel() is polled per page, so Cancel responds
    within a page rather than after the whole file.

    Climbs the tier ladder until the output is at or under the ceiling. If no
    permitted tier gets there the best result is still written and
    under_ceiling is False - it never ships an over-limit file silently.
    """
    opt = opt or OptimizeOptions()
    pk = _pikepdf()
    src_bytes = os.path.getsize(src_path)
    notes = []

    def emit(stage, done=0, total=1):
        if on_progress is not None:
            on_progress(stage, done, total)

    def cancelled():
        return should_cancel is not None and should_cancel()

    emit("Reading", 0, 1)
    # Read the source once. Every pass used to re-open it from disk, which on
    # a network share meant reading a 30 MB scan three times over the wire.
    try:
        with open(src_path, "rb") as fh:
            source_bytes = fh.read()
    except OSError as exc:
        raise PdfOpError(describe_lock_failure(src_path, exc))
    prof = profile_pdf(src_path, data=source_bytes)
    diagnosis = describe_profile(prof)

    if prof.signed and opt.abort_if_signed:
        return OptimizeResult(
            src=src_path, dst=None, ok=False, src_bytes=src_bytes, dst_bytes=0,
            tier="none", tier_index=-1, under_ceiling=False, removed={}, kept={},
            notes=[], diagnosis=diagnosis, signature_voided=False,
            error="This PDF carries a digital signature. Rewriting it would "
                  "void the signature, so it was skipped.",
        )
    signature_voided = bool(prof.signed)
    if signature_voided:
        notes.append(
            "This file carried a DIGITAL SIGNATURE. The optimized copy has "
            "been written, but rewriting a PDF always voids its signature, so "
            "the copy is no longer verifiable. Your original is untouched - "
            "review the copy before sending it, and send the original instead "
            "if the signature has to hold."
        )
    if prof.revisions > 1:
        notes.append(
            f"{plural(prof.revisions - 1, 'earlier saved version')} "
                    f"{'was' if prof.revisions == 2 else 'were'} embedded in "
            "this file and have been discarded."
        )

    out_dir = os.path.join(os.path.dirname(os.path.abspath(src_path)), opt.out_dir_name)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        # Without this the operator saw "Unexpected problem: PermissionError."
        # - no path, no cause, nothing to act on. Verified by injection.
        raise PdfOpError(
            f"Could not create the '{opt.out_dir_name}' folder in "
            f"{os.path.dirname(os.path.abspath(src_path))}. "
            + ("That location is read-only, or you do not have permission to "
               "write to it." if isinstance(exc, PermissionError)
               else f"Windows reported: {exc.strerror or exc}.")
        )
    dest = os.path.join(out_dir, os.path.basename(src_path))
    if not opt.overwrite:
        dest = _unique_destination(dest)

    ladder = []
    for tier in OPTIMIZE_TIERS:
        if tier["i"] >= 2 and not opt.allow_downsample:
            continue
        if tier.get("gray") and not opt.allow_gray:
            continue
        if tier.get("to_bw") and not opt.allow_gray_to_bw:
            continue
        if tier.get("dpi") and tier["dpi"] < opt.min_dpi:
            continue
        ladder.append(tier)

    def run_tier(tier, base_bytes=None):
        """One full pass: sanitize, optionally re-encode images, save.
        Returns (bytes, removed, kept, notes) or raises OptimizeCancelled."""
        tier_notes = []
        pdf = pk.open(io.BytesIO(base_bytes if base_bytes is not None
                                 else source_bytes))
        with pdf:
            removed, kept = sanitize_pdf(pdf, opt.sanitize)
            if tier["images"]:
                n = _recompress_images(
                    pdf, tier, tier_notes,
                    on_page=lambda p, t, nm=tier["name"]: emit(f"{nm}: page {p} of {t}", p, t),
                    should_cancel=should_cancel,
                )
                if n:
                    tier_notes.append(f"{plural(n, 'image')} re-encoded at {tier['name']}.")
            emit(f"Saving {tier['name']}", 1, 1)
            return _save_pdf_bytes(pdf), removed, kept, tier_notes

    best = None            # (size, tier, removed, kept, notes, data)
    attempted = 0
    last = None
    skipped_rungs = 0
    passes = 0

    # ---------------------------------------------------------------- step 1
    # Strip hidden information and rewrite. On a lot of files this alone is
    # enough - a discarded revision history or an embedded attachment can be
    # most of the bulk - and when it is, nothing is re-compressed at all.
    clean_tier = ladder[0] if ladder and not ladder[0]["images"] else OPTIMIZE_TIERS[0]
    emit("Removing hidden information", 0, 1)
    try:
        data, removed, kept, tnotes = run_tier(clean_tier)
        passes += 1
        best = (len(data), clean_tier, removed, kept, notes + tnotes, data)
    except OptimizeCancelled:
        best = None
    except Exception as exc:
        return OptimizeResult(
            src=src_path, dst=None, ok=False, src_bytes=src_bytes, dst_bytes=0,
            tier="none", tier_index=-1, under_ceiling=False, removed={}, kept={},
            notes=[], diagnosis=diagnosis,
            error=f"Could not process the file ({exc}).",
        )

    need_more = best is None or best[0] > opt.ceiling_bytes

    # ---------------------------------------------------------------- forced
    # One pass, accepted unconditionally - even if it comes out LARGER than
    # the cleaned file. That is the contract of a force switch: the operator
    # asked for a flattened document, not for a smaller one. Greyscale at the
    # normal DPI rather than the ladder's black & white last resort, because
    # with no size to hit there is no reason to spend legibility.
    if opt.force_flatten and best is not None and not cancelled():
        emit("Flattening pages to images", 0, 1)
        try:
            data = flatten_to_images(
                source_bytes, dpi=opt.flatten_dpi, quality=45, to_bw=False,
                on_page=lambda pg, t: emit(
                    f"Flattening at {opt.flatten_dpi} DPI: page {pg} of {t}",
                    pg, t),
                should_cancel=should_cancel,
            )
            passes += 1
            best = (
                len(data),
                {"i": 99,
                 "name": f"Flattened to {opt.flatten_dpi} DPI images (forced)",
                 "images": True},
                best[2], {},
                list(notes) + [
                    f"{plural(prof.pages, 'page')} re-rendered as images because "
                    "'Flatten pages to images' was ticked. The searchable "
                    "text layer, annotations and bookmarks are NOT in this "
                    "version - keep the original. The size limit was not "
                    "applied to this run."
                ],
                data,
            )
        except OptimizeCancelled:
            pass
        except Exception as exc:
            return OptimizeResult(
                src=src_path, dst=None, ok=False, src_bytes=src_bytes,
                dst_bytes=0, tier="none", tier_index=-1, under_ceiling=False,
                removed={}, kept={}, notes=notes, diagnosis=diagnosis,
                signature_voided=signature_voided, ceiling_applied=False,
                error=f"Could not flatten the file ({exc}).",
            )
        need_more = False

    # If the bytes that are NOT images already exceed the ceiling, then no
    # amount of image compression can reach it - the arithmetic settles it
    # without running a single pass. Skip straight to flattening.
    #
    # This is measured on the CLEANED candidate, never on the original file.
    # The original's on-disk size includes dead bytes from discarded revisions
    # that the census cannot see, and charging those to "weight that cannot be
    # compressed" made a 34 MB all-images file look like 17 MB of non-image
    # content - which flattened a document that only needed its scans shrunk,
    # destroying its text layer for no reason. Measure the thing you are about
    # to make a decision about.
    non_image_bytes = 0
    if best is not None:
        try:
            with pk.open(io.BytesIO(best[5])) as cleaned:
                cleaned_census = byte_census(cleaned)
            accounted = sum(cleaned_census.values())
            non_image_bytes = (accounted - cleaned_census.get("Scanned images", 0)
                               + max(best[0] - accounted, 0))
        except Exception:
            non_image_bytes = 0
    images_cannot_win = (
        need_more and non_image_bytes > opt.ceiling_bytes and opt.allow_flatten
    )
    if images_cannot_win:
        notes.append(
            f"After cleaning, {non_image_bytes / 1_000_000:.2f} MB of this "
            "file is still not image data - already over the limit on its "
            "own - so compressing the scans could not get there and the pages "
            "were re-rendered instead."
        )

    # ---------------------------------------------------------------- step 2
    image_ladder = [t for t in ladder if t["images"]]
    if need_more and image_ladder and not images_cannot_win and not cancelled():
        if opt.mode == "smallest":
            # Straight to the most aggressive setting allowed. No walking down
            # from high quality - the goal is the smallest readable file, not
            # the best one that happens to fit.
            aggressive = image_ladder[-1]
            emit(f"Optimizing at {aggressive['name']}", 0, 1)
            try:
                data, removed, kept, tnotes = run_tier(aggressive)
                passes += 1
                # Step 3 - strip hidden information again, on the optimized
                # file. Re-encoding every image orphans the old streams, and
                # this is the pass that clears them. It is measured, not
                # ritual: what it actually removes is reported.
                emit("Removing hidden information again", 1, 1)
                data2, removed2, _kept2, _n2 = run_tier(clean_tier, base_bytes=data)
                passes += 1
                if len(data2) <= len(data):
                    if removed2:
                        tnotes.append(
                            "A second hidden-information pass after optimizing "
                            "removed: " + ", ".join(sorted(removed2)) + "."
                        )
                    gained = len(data) - len(data2)
                    tnotes.append(
                        f"Second hidden-information pass recovered a further "
                        f"{gained / 1000:.0f} KB." if gained > 0 else
                        "Second hidden-information pass found nothing left to remove."
                    )
                    data = data2
                    for key, val in removed2.items():
                        removed[key] = removed.get(key, 0) + val
                if best is None or len(data) < best[0]:
                    best = (len(data), aggressive, removed, kept, notes + tnotes, data)
            except OptimizeCancelled:
                pass
            except Exception:
                pass
        else:
            # "quality" mode: the old ladder, best setting that still fits.
            for pos, tier in enumerate(image_ladder):
                if cancelled():
                    break
                if last is not None and tier.get("dpi") and pos < len(image_ladder) - 1:
                    last_dpi, last_factor, last_size = last
                    est = last_size * ((tier["dpi"] / last_dpi) ** 2) * \
                        (tier.get("factor", 1.0) / last_factor)
                    if est > opt.ceiling_bytes * 1.15:
                        skipped_rungs += 1
                        continue
                attempted += 1
                emit(f"Trying {tier['name']}", 0, 1)
                try:
                    data, removed, kept, tnotes = run_tier(tier)
                    passes += 1
                except OptimizeCancelled:
                    break
                except Exception:
                    continue
                if tier.get("dpi"):
                    last = (float(tier["dpi"]), tier.get("factor", 1.0), float(len(data)))
                if best is None or len(data) < best[0]:
                    best = (len(data), tier, removed, kept, notes + tnotes, data)
                if len(data) <= opt.ceiling_bytes:
                    best = (len(data), tier, removed, kept, notes + tnotes, data)
                    break

    # ---------------------------------------------------------------- step 4
    # Last resort. Only runs when the file is still over the limit, flatten is
    # explicitly allowed, and there is something to gain - i.e. the weight is
    # NOT mostly images, so squeezing images further cannot help.
    if (opt.allow_flatten and best is not None and not opt.force_flatten
            and best[0] > opt.ceiling_bytes and not cancelled()):
        emit("Flattening pages to images", 0, 1)
        for dpi, quality, bw in ((opt.flatten_dpi, 45, False),
                                 (max(96, opt.flatten_dpi - 24), 35, False),
                                 (opt.flatten_dpi, 0, True)):
            if cancelled():
                break
            # No gate on this one. By the time 120 DPI and 96 DPI greyscale
            # have both missed, the only alternative is handing back a file
            # that does not fit - so black & white is strictly better than
            # failing, and it is reported by name in the result.
            try:
                data = flatten_to_images(
                    source_bytes, dpi=dpi, quality=quality, to_bw=bw,
                    on_page=lambda p, t, d=dpi: emit(
                        f"Flattening at {d} DPI: page {p} of {t}", p, t),
                    should_cancel=should_cancel,
                )
                passes += 1
            except OptimizeCancelled:
                break
            except Exception as exc:
                notes.append(f"Flattening failed ({type(exc).__name__}).")
                break
            label = (f"Flattened to {dpi} DPI black & white" if bw
                     else f"Flattened to {dpi} DPI images")
            fnotes = list(notes) + [
                f"{plural(prof.pages, 'page')} re-rendered as images. "
                "The searchable text layer, annotations and bookmarks are NOT "
                "in this version - keep the original."
            ]
            ftier = {"i": 99, "name": label, "images": True}
            if len(data) < best[0]:
                best = (len(data), ftier, best[2], {}, fnotes, data)
            if len(data) <= opt.ceiling_bytes:
                break

    if best is None:
        return OptimizeResult(
            src=src_path, dst=None, ok=False, src_bytes=src_bytes, dst_bytes=0,
            tier="none", tier_index=-1, under_ceiling=False, removed={}, kept={},
            notes=notes, diagnosis=diagnosis, signature_voided=signature_voided,
            ceiling_applied=not opt.force_flatten,
            error=("Cancelled before anything was written."
                   if cancelled() else "Every setting failed on this file."),
        )

    size, tier, removed, kept, tier_notes, data = best
    emit("Writing", 1, 1)
    try:
        _write_bytes_atomic(dest, data)
    except OSError as exc:
        raise PdfOpError(
            f"Could not write {os.path.basename(dest)} into {out_dir}. "
            + ("There is not enough free space." if getattr(exc, "errno", None) == 28
               else "That location is read-only, or you do not have permission "
                    "to write to it." if isinstance(exc, PermissionError)
               else f"Windows reported: {exc.strerror or exc}.")
        )
    if os.path.basename(dest) != os.path.basename(src_path):
        tier_notes.append(
            f"An optimized copy already existed, so this one was saved as "
            f"'{os.path.basename(dest)}' rather than replacing it."
        )

    if skipped_rungs:
        tier_notes.append(
            f"{plural(skipped_rungs, 'setting')} were skipped because a measured "
            "attempt showed they could not get small enough."
        )
    tier_notes.insert(0, f"{passes} pass(es) over the file.")

    if size > opt.ceiling_bytes and not opt.force_flatten:
        over = (size - opt.ceiling_bytes) / 1_000_000
        # The advice must never name a control the operator does not have.
        # Every escalation is automatic now, so if this line is reached the
        # tool has already done everything it can and the only remaining
        # answer is a smaller document.
        if opt.allow_flatten:
            tier_notes.append(
                f"Could not reach the {opt.ceiling_bytes / 1_000_000:.1f} MB "
                f"limit - still {over:.2f} MB over, after re-rendering every "
                f"page as an image. This {prof.pages}-page document is simply "
                "too long for that size: split it and file it in parts."
            )
        else:
            tier_notes.append(
                f"Could not reach the {opt.ceiling_bytes / 1_000_000:.1f} MB "
                f"limit - still {over:.2f} MB over. Re-rendering the pages as "
                "images is switched off; with it on this would fit, or the "
                "document needs splitting."
            )

    return OptimizeResult(
        src=src_path, dst=dest, ok=True, src_bytes=src_bytes, dst_bytes=size,
        tier=tier["name"], tier_index=tier["i"],
        under_ceiling=size <= opt.ceiling_bytes,
        removed=removed, kept=kept, notes=tier_notes, diagnosis=diagnosis,
        passes=passes, signature_voided=signature_voided,
        ceiling_applied=not opt.force_flatten,
    )


def verify_optimized(src_path: str, dst_path: str) -> dict:
    """
    Independent check of an optimized file. Reopens from disk and reads
    nothing from the optimize run - a tool reporting OK has told you it ran,
    not what the file now says.
    """
    pk = _pikepdf()
    report = {"ok": True, "problems": []}

    with open(dst_path, "rb") as fh:
        raw = fh.read()
    report["revisions"] = raw.count(b"%%EOF")
    if report["revisions"] != 1:
        report["ok"] = False
        report["problems"].append(
            f"{report['revisions']} %%EOF markers - revision history may remain"
        )

    with pk.open(src_path) as a, pk.open(dst_path) as b:
        report["pages_src"] = len(a.pages)
        report["pages_dst"] = len(b.pages)
        if len(a.pages) != len(b.pages):
            report["ok"] = False
            report["problems"].append("page count changed")

        root = b.Root
        residual = []
        if "/Metadata" in root:
            residual.append("/Metadata")
        try:
            if len(b.docinfo):
                residual.append("/Info")
        except Exception:
            pass
        for key in ("/OpenAction", "/AA", "/PieceInfo", "/Legacy"):
            if key in root:
                residual.append(key)
        names = root.get("/Names")
        if names is not None:
            for key in ("/JavaScript", "/EmbeddedFiles"):
                if key in names:
                    residual.append("/Names" + key)
        for pno, page in enumerate(b.pages, start=1):
            for key in ("/Thumb", "/PieceInfo", "/AA", "/Metadata"):
                if key in page.obj:
                    residual.append(f"p{pno}{key}")
        report["residual"] = residual
        if residual:
            report["ok"] = False
            report["problems"].append("hidden data still present: " + ", ".join(residual))

    report["src_bytes"] = os.path.getsize(src_path)
    report["dst_bytes"] = os.path.getsize(dst_path)
    return report



class RenameRuleError(Exception):
    """The rule itself is malformed (bad regex, bad scope)."""


class RenameExecError(Exception):
    """Execution refused or failed."""


@dataclass
class RenameRule:
    search: str = ""
    replace: str = ""
    use_regex: bool = False
    case_sensitive: bool = False
    match_scope: str = "name"       # name | ext | both
    transform: str = "none"         # none | upper | lower | title
    enumerate_enabled: bool = False
    enumerate_from: int = 1
    enumerate_pad: int = 1


@dataclass
class RenameRow:
    src: str
    dst: str
    status: str
    note: str = ""

    @property
    def src_name(self) -> str:
        return os.path.basename(self.src)

    @property
    def dst_name(self) -> str:
        return os.path.basename(self.dst) if self.dst else ""


class RenamePlan:
    def __init__(self, rows, copies: bool = False):
        self.rows = rows
        #: True when the originals are KEPT and the output is a copy. Changes
        #: what execute does, and - more importantly - what undo does.
        self.copies = copies

    @property
    def ok_rows(self):
        return [r for r in self.rows if r.status == ST_OK]

    @property
    def blocking_rows(self):
        return [r for r in self.rows if r.status in BLOCKING_STATUSES]

    @property
    def ok_count(self) -> int:
        return len(self.ok_rows)

    @property
    def blocking_count(self) -> int:
        return len(self.blocking_rows)

    @property
    def is_applicable(self) -> bool:
        return self.ok_count > 0 and self.blocking_count == 0


# ---------------------------------------------------------------------
# Rule application
# ---------------------------------------------------------------------

def _title_case(text: str) -> str:
    # str.title() mangles possessives ("smith's" -> "Smith'S") because it
    # treats the apostrophe as a word break. Apostrophes are word-internal here.
    return re.sub(
        r"[A-Za-z]+(?:['\u2019][A-Za-z]+)*",
        lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(),
        text,
    )


def _apply_transform(text: str, mode: str) -> str:
    if mode == "upper":
        return text.upper()
    if mode == "lower":
        return text.lower()
    if mode == "title":
        return _title_case(text)
    return text


def _literal_replace(text: str, search: str, repl: str, case_sensitive: bool) -> str:
    if case_sensitive:
        return text.replace(search, repl)
    # lambda replacement keeps backslashes in `repl` literal
    return re.sub(re.escape(search), lambda _m: repl, text, flags=re.IGNORECASE)


def _compile(rule: RenameRule):
    if not rule.use_regex or not rule.search:
        return None
    flags = 0 if rule.case_sensitive else re.IGNORECASE
    try:
        rx = re.compile(rule.search, flags)
    except re.error as e:
        raise RenameRuleError(f"That is not a valid regular expression ({e.msg}). Untick 'Use regular expressions' to search for the text exactly as typed.") from e
    refusal = catastrophic_regex(rule.search)
    if refusal:
        raise RenameRuleError(refusal)
    return rx


def _sub_skip_empty(pattern, repl: str, text: str) -> str:
    """re.sub, but zero-length matches are ignored.

    Plain re.sub('.*', 'X', 'zebra') returns 'XX': the pattern matches the whole
    string and then the empty string at the end. Staff typing '.*' to mean
    "the whole name" would silently get the replacement twice. Zero-width
    matches are never what a rename rule intends, so they are dropped.
    """
    out = []
    last = 0
    for m in pattern.finditer(text):
        if m.start() == m.end():
            continue
        out.append(text[last:m.start()])
        out.append(m.expand(repl))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def _rewrite(text: str, rule: RenameRule, pattern, index: int) -> str:
    repl = rule.replace
    if rule.enumerate_enabled:
        num = str(rule.enumerate_from + index).zfill(max(1, rule.enumerate_pad))
        repl = repl.replace(INCREMENT_TOKEN, num)
    elif INCREMENT_TOKEN in repl:
        # The Bulk Renamer no longer has a "Number files from" option. A saved
        # preset that still says ${increment} would otherwise rename every
        # file to the literal text "${increment}".
        raise RenameRuleError("Numbering files was removed from the Bulk Renamer, so "
                              "${increment} no longer works. Take it out of the Replace box.")

    if rule.search:
        if pattern is not None:
            try:
                out = _sub_skip_empty(pattern, repl, text)
            except re.error as e:
                raise RenameRuleError(f"Invalid replacement pattern: {e}") from e
        else:
            out = _literal_replace(text, rule.search, repl, rule.case_sensitive)
    else:
        out = text

    return _apply_transform(out, rule.transform)


def _new_basename(base: str, rule: RenameRule, pattern, index: int) -> str:
    if rule.match_scope == "both":
        return _rewrite(base, rule, pattern, index)

    stem, ext = os.path.splitext(base)
    if rule.match_scope == "name":
        return _rewrite(stem, rule, pattern, index) + ext
    if rule.match_scope == "ext":
        return stem + _rewrite(ext, rule, pattern, index)
    raise RenameRuleError(f"Unknown match scope: {rule.match_scope}")


def _validate_basename(new_base: str, folder: str):
    """Return (status, note) — (ST_OK, '') if the name is usable."""
    if not new_base.strip():
        return ST_EMPTY, "resulting name is empty"

    bad = [c for c in WINDOWS_ILLEGAL_CHARS if c in new_base]
    if bad:
        return ST_INVALID, f"illegal {plural(len(bad), 'character')}: " + " ".join(bad)

    if new_base.endswith(" ") or new_base.endswith("."):
        return ST_INVALID, "name ends with a space or dot"

    # Windows silently strips a trailing space or dot from the stem, so
    # "a .pdf" quietly becomes "a.pdf" — a rename that does not match preview.
    # partition(".") takes the FIRST dot, so "report.v1 .pdf" had a stem of
    # "report" and its trailing space went undetected - the preview said the
    # rename was fine and Windows then silently stored a different name.
    root = os.path.splitext(new_base)[0]
    _sep = _tail = ""
    if not root:
        return ST_EMPTY, "resulting name has no text before the extension"
    if root.endswith(" ") or root.endswith("."):
        return ST_INVALID, "name has a trailing space or dot before the extension"

    stem = root
    if stem.upper() in WINDOWS_RESERVED:
        return ST_RESERVED, f"'{stem}' is a reserved Windows device name"

    if len(os.path.join(folder, new_base)) >= max_path_len():
        return ST_TOO_LONG, f"full path would be {len(os.path.join(folder, new_base))} chars (limit {max_path_len()})"

    return ST_OK, ""


def build_plan(paths, rule: RenameRule) -> RenamePlan:
    """Build a full rename plan. Never touches the filesystem except to stat.

    Raises RenameRuleError for a malformed rule. Individual file problems are
    reported as row statuses, not exceptions.
    """
    if rule.match_scope not in SCOPES:
        raise RenameRuleError(f"Unknown match scope: {rule.match_scope}")
    if rule.transform not in TRANSFORMS:
        raise RenameRuleError(f"Unknown transform: {rule.transform}")

    pattern = _compile(rule)

    # Stable ordering: enumerate numbering must not depend on listdir order.
    ordered = sorted(paths, key=lambda p: (os.path.dirname(p).casefold(),
                                           os.path.basename(p).casefold()))
    src_keys = {os.path.abspath(p).casefold() for p in ordered}

    entries = []
    for index, src in enumerate(ordered):
        folder, base = os.path.split(os.path.abspath(src))
        new_base = _new_basename(base, rule, pattern, index)
        entries.append((os.path.abspath(src), folder, new_base, folder, None, ""))

    return _assemble_plan(entries, movable=src_keys)


def _assemble_plan(entries, movable=frozenset(), copies=False):
    """Validate names, detect collisions, and build the RenamePlan.

    Shared by BOTH planners - the find/replace rule and Auto Rename's profile
    profiles. Deliberately one implementation: a second copy of collision
    detection is the kind of duplication that stays correct for a month and
    then diverges silently on the path that destroys files.

    entries: (src, src_folder, new_base, dst_folder, pre_status, pre_note)
    movable: casefolded source paths that WILL be renamed out of the way. Only
             non-empty for an in-place rename; when the output goes to another
             folder, or is a copy, nothing moves and an existing file always
             blocks.
    """
    rows = []
    claims = {}     # casefolded destination path -> list of row indexes
    dir_cache = {}  # folder -> set of casefolded full paths already on disk

    def existing_in(folder: str) -> set:
        """One listdir per folder instead of one stat per file.

        On a local disk this is ~3x faster; over SMB, where each stat is a
        round trip, the difference is far larger.
        """
        key = folder.casefold()
        if key not in dir_cache:
            try:
                dir_cache[key] = {
                    os.path.join(folder, n).casefold() for n in os.listdir(folder)
                }
            except OSError:
                # A destination folder that does not exist yet is not an error;
                # it is created at execute time.
                dir_cache[key] = set()
        return dir_cache[key]

    for src, folder, new_base, dst_folder, pre_status, pre_note in entries:
        if pre_status:
            rows.append(RenameRow(src=src, dst="", status=pre_status, note=pre_note))
            continue

        dst = os.path.join(dst_folder, new_base)
        status, note = _validate_basename(new_base, dst_folder)

        if status == ST_OK:
            if os.path.abspath(dst).casefold() == src.casefold():
                status = ST_UNCHANGED
            elif (os.path.abspath(dst).casefold() in existing_in(dst_folder)
                  and os.path.abspath(dst).casefold() not in movable):
                # A file already there that is NOT part of this batch, so it
                # will never be renamed out of the way.
                status, note = ST_EXISTS, "a different file already uses that name"

        rows.append(RenameRow(src=src, dst=dst, status=status, note=note))

        # Collision detection includes unchanged rows: an unchanged file still
        # occupies its name and would be overwritten.
        if status in (ST_OK, ST_UNCHANGED):
            claims.setdefault(os.path.abspath(dst).casefold(), []).append(len(rows) - 1)

    for _key, idxs in claims.items():
        if len(idxs) > 1:
            names = ", ".join(sorted({rows[i].src_name for i in idxs}))
            for i in idxs:
                rows[i].status = ST_COLLISION
                rows[i].note = f"{len(idxs)} files would end up named the same ({names})"

    return RenamePlan(rows, copies=copies)


# ---------------------------------------------------------------------
# Execution — always two-phase, so cycles and case-only renames are safe
# ---------------------------------------------------------------------

def _log_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    path = os.path.join(base, APP_SHORT)
    os.makedirs(path, exist_ok=True)
    return path


def move_log_path() -> str:
    """The Bulk File Mover's own undo record, separate from the Renamer's so
    one cannot clobber the other."""
    return os.path.join(_log_dir(), "last_move.jsonl")


def match_by_number(names, numbers, separators: str = None):
    """
    (matched, unmatched_numbers) for a list of file names.

    `startswith` alone is unanchored: typing 123 also takes 1234-Jones.pdf,
    so one mistyped digit swept hundreds of files. A number now has to be
    followed by the end of the stem or a separator.
    """
    if separators is None:
        separators = setting("mover_separators")
    matched, used = [], set()
    for name in names:
        stem = os.path.splitext(name)[0]
        for num in numbers:
            if not stem.startswith(num):
                continue
            rest = stem[len(num):]
            boundary = ((rest[0] in separators) if separators
                        else not rest[0].isalnum()) if rest else True
            if boundary:
                matched.append((name, num))
                used.add(num)
                break
    return matched, [n for n in numbers if n not in used]


def undo_log_path() -> str:
    """The CURRENT undo record. Older runs, up to the Advanced 'undo depth',
    sit beside it as <name>.<epoch>.hist and are promoted back one at a
    time as runs are undone."""
    return os.path.join(_log_dir(), "last_rename.jsonl")


def _history_files(log_path: str) -> list:
    """Archived logs for log_path, oldest first."""
    folder, base = os.path.split(log_path)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = []
    for n in names:
        if n.startswith(base + ".") and n.endswith(".hist"):
            try:
                stamp = int(n[len(base) + 1:-5])
            except ValueError:
                continue
            out.append((stamp, os.path.join(folder, n)))
    return [p for _s, p in sorted(out)]


def archive_undo_log(log_path: str, keep: int = None) -> None:
    """Called just before a NEW log is published at log_path: shelve the one
    that is there, and prune so at most `keep` runs remain undoable."""
    keep = int(keep if keep is not None else setting("undo_depth"))
    if os.path.exists(log_path):
        if keep > 1:
            stamp = int(time.time() * 1000)
            hist = _history_files(log_path)
            if hist:
                # Monotonic even if two runs land in one millisecond or the
                # clock steps back: always newer than what is already there.
                newest = int(hist[-1][len(log_path) + 1:-5])
                stamp = max(stamp, newest + 1)
            try:
                os.replace(log_path, f"{log_path}.{stamp}.hist")
            except OSError:
                pass
    prune_undo_history(log_path, keep)


def prune_undo_history(log_path: str, keep: int) -> None:
    """Keep at most keep-1 archived runs beside the current log."""
    hist = _history_files(log_path)
    for old in hist[:max(0, len(hist) - (int(keep) - 1))]:
        try:
            os.remove(old)
        except OSError:
            pass


def promote_undo_history(log_path: str) -> bool:
    """After a log was consumed by undo(), make the next-newest run current."""
    hist = _history_files(log_path)
    if not hist:
        return False
    try:
        os.replace(hist[-1], log_path)
        return True
    except OSError:
        return False


def audit_log_path() -> str:
    """The AUDIT record. Append-only, never rewritten, never truncated.

    Separate from the undo log on purpose. The undo log answers "put the last
    batch back" and is overwritten by the next run; this answers "what did we
    rename, from what, to what, and when" months later - which for anyone who
    keeps records is a question that eventually gets asked and, before
    this existed, could not be answered at all.
    """
    return os.path.join(_log_dir(), "rename_audit.jsonl")


AUDIT_MAX_BYTES = 5 * 1024 * 1024
AUDIT_KEEP = 3


def _rotate_audit(path: str) -> None:
    """Roll at 5MB, keep 3. An audit log that grows without bound eventually
    gets deleted by somebody freeing disk space, which loses everything - a
    bounded one that keeps its recent history survives."""
    try:
        if not os.path.exists(path) or os.path.getsize(path) < AUDIT_MAX_BYTES:
            return
        for i in range(AUDIT_KEEP - 1, 0, -1):
            older, newer = f"{path}.{i + 1}", f"{path}.{i}"
            if os.path.exists(newer):
                os.replace(newer, older)
        os.replace(path, f"{path}.1")
    except OSError:
        pass            # a full disk must not stop a rename


def _audit_user() -> str:
    for key in ("USERNAME", "USER", "LOGNAME"):
        who = os.environ.get(key)
        if who:
            return who
    return "unknown"


def write_audit(records) -> None:
    """Append a batch to the audit log. Never raises."""
    records = list(records)
    if not records:
        return
    path = audit_log_path()
    try:
        _rotate_audit(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            for rec in records:
                rec = dict(rec)
                rec.setdefault("at", stamp)
                rec.setdefault("user", _audit_user())
                rec.setdefault("app", APP_VERSION)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        pass            # the audit is a record, never a gate


def execute_plan(plan: RenamePlan, log_path: str = None, progress=None,
                 rule_label: str = ""):
    """Apply a plan. Returns (renamed_count, failures, log_path).

    Two-phase: every source moves to a unique temp name first, then to its
    final name. This makes swaps (a->b, b->a) and case-only renames
    (file.pdf -> File.pdf) correct on both case-sensitive and
    case-insensitive filesystems.

    Every completed step is flushed to a JSONL log immediately, so a crash
    mid-batch still leaves a log that `undo` can replay.
    """
    if plan.blocking_count:
        raise RenameExecError(
            f"{plural(plan.blocking_count)} " + ("has" if plan.blocking_count == 1 else "have") + " blocking problems. "
            "Resolve them or exclude them before renaming."
        )
    ops = plan.ok_rows
    if not ops:
        raise RenameExecError("Nothing to rename.")

    log_path = log_path or undo_log_path()
    failures = []
    staged = []  # (tmp, orig, dst)
    src_hidden = {}   # orig -> was the SOURCE hidden before we touched it

    # Opened "w" on the real path, this truncated the PREVIOUS batch's undo
    # log before a single rename had succeeded - a run that failed outright
    # left the operator with zero undo levels. Write to a sibling and publish
    # it only at the end.
    log_path_final = log_path
    log_path = log_path + ".new"
    with open(log_path, "w", encoding="utf-8") as log:
        def emit(record):
            log.write(json.dumps(record) + "\n")
            log.flush()

        mode = "copy" if getattr(plan, "copies", False) else "move"
        emit({"step": "begin", "count": len(ops), "mode": mode})

        # Phase A — stage every file under a unique temp name.
        #
        # In COPY mode the original is never touched: copy2 writes a temp copy
        # in the DESTINATION folder, so phase B is always a same-folder rename
        # and can never fail part-way across a filesystem boundary with the
        # source already gone. copy2 keeps the timestamps - on a legal document
        # the modified date is part of the record, not decoration.
        for row in ops:
            # The destination folder is created for BOTH modes. Only the copy
            # path needed it during development, so the move-into-a-new-folder
            # case shipped without it: phase A staged the file, phase B tried to
            # rename into a folder that did not exist, and the run left temp
            # files behind. Found by the suite, not by inspection.
            try:
                os.makedirs(fs_path(os.path.dirname(row.dst)), exist_ok=True)
            except OSError as e:
                failures.append(describe_lock_failure(row.src, e))
                continue
            # Copies stage in the DESTINATION folder, so phase B is always a
            # same-folder rename. Moves stage in the SOURCE folder, which is
            # what makes in-place swaps and case-only renames correct.
            folder = os.path.dirname(row.dst) if mode == "copy" else os.path.dirname(row.src)
            tmp = os.path.join(folder, TEMP_PREFIX + uuid.uuid4().hex[:12])
            src_hidden[row.src] = file_is_hidden(row.src)
            try:
                if mode == "copy":
                    shutil.copy2(fs_path(row.src), fs_path(tmp))
                else:
                    os.rename(fs_path(row.src), fs_path(tmp))
            except OSError as e:
                failures.append(describe_lock_failure(row.src, e))
                continue
            staged.append((tmp, row.src, row.dst))
            emit({"step": "stage", "tmp": tmp, "orig": row.src, "mode": mode})

        # Phase B — move into final names
        renamed = 0
        for i, (tmp, orig, dst) in enumerate(staged):
            try:
                os.rename(fs_path(tmp), fs_path(dst))
            except OSError as e:
                failures.append(describe_lock_failure(orig, e))
                continue
            if not src_hidden.get(orig, False):
                clear_hidden(dst)
            renamed += 1
            emit({"step": "final", "dst": dst, "orig": orig, "mode": mode})
            if progress:
                progress(i + 1, len(staged))

        emit({"step": "end", "renamed": renamed})

    for _f in {os.path.dirname(d) for _t, _o, d in staged}:
        notify_shell_folder(_f)

    # The audit is written AFTER the run, from what actually happened - not
    # from the plan. A plan says what was intended; this says what occurred.
    write_audit([{"action": mode, "rule": rule_label,
                  "from": os.path.basename(orig), "to": os.path.basename(dst),
                  "folder": os.path.dirname(orig), "dest": os.path.dirname(dst)}
                 for _tmp, orig, dst in staged
                 if os.path.exists(dst)])

    try:
        if renamed:
            archive_undo_log(log_path_final)
            os.replace(log_path, log_path_final)
        elif os.path.exists(log_path):
            os.remove(log_path)      # nothing happened - keep the previous log
    except OSError:
        pass
    return renamed, failures, log_path_final


def undo(log_path: str = None):
    """Replay a log backwards. Returns (restored_count, failures).

    Handles a partial log from a crashed run: `stage` entries with no matching
    `final` are files still sitting under a temp name.
    """
    log_path = log_path or undo_log_path()
    if not os.path.exists(log_path):
        raise RenameExecError("No previous rename to undo.")

    finals, stages = [], []
    mode = "move"
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # truncated final line from a hard kill
            if rec.get("mode") == "copy":
                mode = "copy"
            if rec.get("step") == "final":
                finals.append((rec["dst"], rec["orig"]))
            elif rec.get("step") == "stage":
                stages.append((rec["tmp"], rec["orig"]))

        # Any staged file that never reached a final name is still a temp file.
    finalized_origs = {orig for _dst, orig in finals}
    orphans = [(tmp, orig) for tmp, orig in stages if orig not in finalized_origs]

    if mode == "copy":
        # A COPY run is undone by DELETING the copies it made. The originals
        # were never touched, so there is nothing to move back. Three guards,
        # because this is the only place in the app that deletes anything:
        # only files this log recorded creating, only while they are still
        # where it left them, and never a file that is its own original.
        removed, failures = 0, []
        for current, original in finals + orphans:
            if not os.path.exists(current):
                continue
            if os.path.abspath(current).casefold() == os.path.abspath(original).casefold():
                failures.append(
                    f"{os.path.basename(current)}: refused - that is the original file")
                continue
            try:
                os.remove(current)
                removed += 1
            except OSError as e:
                failures.append(f"{os.path.basename(current)}: {e}")
        for folder in {os.path.dirname(c) for c, _o in finals + orphans}:
            try:
                if folder and os.path.isdir(folder) and not os.listdir(folder):
                    os.rmdir(folder)      # only ever if the undo emptied it
            except OSError:
                pass
        write_audit([{"action": "undo-copy", "rule": "",
                      "from": os.path.basename(c), "to": "",
                      "folder": os.path.dirname(c), "dest": ""}
                     for c, _o in finals + orphans])
        if removed and not failures:
            try:
                os.remove(log_path)
                promote_undo_history(log_path)
            except OSError:
                pass
        return removed, failures

    restored = 0
    failures = []
    pairs = list(reversed(finals + orphans))

    # Undo is subject to exactly the same cycle and case-only hazards as the
    # forward pass: undoing a swap (a->b, b->a) by renaming straight back finds
    # each target still occupied by its partner. Stage everything first.
    # AUDIT 2026-08-13: this set used to be built from the ORIGINAL names in
    # `pairs`, so the "something else is sitting at the original name" clause
    # below compared a value against a set derived from that same value and
    # could never fire - undo renamed straight over a legitimate new file.
    #
    # The question the guard is actually asking is "will whatever is sitting
    # at this original name be moved out of the way by this same undo?" - which
    # is true only for the files this undo is about to rename AWAY. So the set
    # is the CURRENT names, not the original ones.
    originals = {os.path.abspath(cur).casefold() for cur, _orig in pairs}
    staged = []
    hidden_before = {}
    for current, original in pairs:
        if not os.path.exists(current):
            failures.append(f"{os.path.basename(current)}: no longer present")
            continue
        cur_abs = os.path.abspath(current).casefold()
        if (os.path.exists(original)
                and os.path.abspath(original).casefold() != cur_abs
                and os.path.abspath(original).casefold() not in originals):
            failures.append(
                f"{os.path.basename(original)}: something else now occupies the original name"
            )
            continue
        tmp = os.path.join(os.path.dirname(current), TEMP_PREFIX + uuid.uuid4().hex[:12])
        cur_hidden = file_is_hidden(current)
        try:
            os.rename(fs_path(current), fs_path(tmp))
            hidden_before[original] = cur_hidden
            staged.append((tmp, original))
        except OSError as e:
            failures.append(f"{os.path.basename(current)}: {e}")

    for tmp, original in staged:
        try:
            os.rename(fs_path(tmp), fs_path(original))
            if not hidden_before.get(original, False):
                clear_hidden(original)
            restored += 1
        except OSError as e:
            failures.append(f"{os.path.basename(original)}: {e}")
    write_audit([{"action": "undo", "rule": "", "from": "",
                  "to": os.path.basename(o), "folder": os.path.dirname(o),
                  "dest": os.path.dirname(o)} for _t, o in staged])

    if restored and not failures:
        try:
            os.remove(log_path)
            promote_undo_history(log_path)
        except OSError:
            pass

    return restored, failures


# ---------------------------------------------------------------------
# Presets — a plain JSON file, not the Windows registry, so it can be
# backed up, diffed and copied between machines.

# =====================================================================
# Auto Rename — classification by profile
# =====================================================================
#
# A THIRD rule kind alongside find/replace and case/enumerate. It extracts an
# ID from the incoming filename, classifies the rest against an ORDERED keyword
# table, and rebuilds the name from a template. That is the shape
# a keyword classifier implements, and it is why a find/replace engine cannot
# express it: the original name is discarded, not edited.
#
# Planning, validation, collision detection, two-phase execution and the undo
# journal are NOT reimplemented here - this section produces names and hands
# them to _assemble_plan and execute_plan like any other rule.

PROFILE_SCHEMA_VERSION = 1

MATCH_MODES = ("contains", "word", "starts", "ends", "any", "not", "regex")

#: Plain-language labels. "contains" is the blunt instrument and the one that
#: causes surprises - it is why a 'pet' row also fires on "Carpet".
MATCH_MODE_LABELS = {
    "contains": "contains",
    "word":     "contains the whole word",
    "starts":   "starts with",
    "ends":     "ends with (before the extension)",
    "any":      "contains any of these words",
    "not":      "does NOT contain",
    "regex":    "matches the pattern",
}
MATCH_MODE_HELP = {
    "contains": "Anywhere in the name. Watch out: 'pet' also matches 'Carpet'.",
    "word":     "Only as a separate word, so 'pet' will not match 'Carpet'.",
    "starts":   "The name begins with this, after the ID.",
    "ends":     "The name ends with this, ignoring the file extension.",
    "any":      "Several words separated by commas - any one of them matches.",
    "not":      "Matches every file that does NOT contain this. Useful as a "
                "catch-all on the LAST row.",
    "regex":    "For advanced use. Simple patterns only.",
}
#: Short labels for the in-table dropdown. The control pane is ~420px wide and
#: the long labels squeezed the two columns that actually matter - the keyword
#: and its ending - down to an ellipsis. The full wording stays in
#: MATCH_MODE_HELP, which is shown under the table for the selected row.
MATCH_MODE_SHORT = {
    "contains": "contains",
    "word":     "whole word",
    "starts":   "starts with",
    "ends":     "ends with",
    "any":      "any of these",
    "not":      "does NOT have",
    "regex":    "pattern",
}

NO_MATCH_ACTIONS = ("skip", "keep", "fallback")
TEMPLATE_FIELDS = ("id", "suffix", "ext", "stem", "orig", "keyword")

# ---------------------------------------------------------------------
# Regex safety
# ---------------------------------------------------------------------
# A staff-authored pattern is untrusted input, and catastrophic backtracking is
# a real, reachable hang: `(a+)+$` against a 40-character filename never
# returns, freezing the app with no way out.
#
# FOUR FIXES WERE TRIED AND REJECTED, recorded so nobody re-tries them:
#   * A timeout - Python's `re` has none.
#   * SIGALRM - POSIX only; this ships to Windows.
#   * A watchdog thread with a time budget - DOES NOT WORK, and the failure
#     looks like success: `re` holds the GIL inside its C matching loop, so the
#     watchdog starves the main thread. The budget expires and the main thread
#     still cannot run the next bytecode. Measured, not assumed.
#   * The `regex` module (which has a timeout) - a new dependency, and this
#     app's dependency surface is fixed.
#
# WHAT IS USED INSTEAD: a whitelist grammar gate. Rather than detect dangerous
# patterns (undecidable in general, and any blacklist is incomplete), accept
# only patterns whose structure PROVES bounded backtracking. Catastrophic
# behaviour needs ambiguity - a quantifier over something that can match the
# same text more than one way. Forbid that and the hazard is gone by
# construction, with no thread, no process and no platform dependency.
#
# The cost is stated rather than hidden: some legitimate complex patterns are
# refused. These are filename patterns; the trade is worth it. The non-regex
# match modes are linear and are never gated.

MAX_PATTERN_LEN = 200
MAX_QUANTIFIERS = 8
_QUANT = frozenset("*+?{")      # membership on a set: "" is not a member
_BACKREF = re.compile(r"\\[1-9]")
_LOOKAROUND = re.compile(r"\(\?(=|!|<=|<!)")


def _regex_groups(pattern: str):
    """(body, is_quantified) for every ( ) group, honouring escapes."""
    stack, i, n, out = [], 0, len(pattern), []
    while i < n:
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if c == "(":
            stack.append(i)
        elif c == ")" and stack:
            start = stack.pop()
            nxt = pattern[i + 1] if i + 1 < n else ""
            out.append((pattern[start + 1:i], nxt in _QUANT))
        i += 1
    return out


def catastrophic_regex(pattern: str):
    """The narrow gate for Find and replace. Returns a plain-language refusal
    only for the two shapes that backtrack EXPONENTIALLY - a quantified group
    that already repeats, and a quantified group holding an 'or' - and None for
    everything else, so look-arounds, back-references and long patterns keep
    working there. (Auto Rename keeps the stricter screen_regex.) This runs on
    the UI thread on every keystroke, so one such pattern froze the window:
    (a+)+$ against a 39-character name did not return in 20 seconds."""
    for body, quantified in _regex_groups(pattern):
        if not quantified:
            continue
        if any(q in body for q in "*+"):
            return ("That pattern repeats a group that already repeats (something "
                    "like (a+)+ ), which can make the program freeze. Usually the "
                    "inner + or * can simply be removed.")
        if "|" in body:
            return ("That pattern repeats a group containing 'or' (a|b), which can "
                    "make the program freeze. Repeat a character class like [ab]+ "
                    "instead.")
    return None


def screen_regex(pattern: str):
    """The gate. Returns a plain-language refusal, or None to accept.

    Refuses anything it cannot prove safe. That is the point: a pattern this
    accepts cannot backtrack catastrophically; one it refuses might have been
    fine.
    """
    if not pattern:
        return "The pattern is empty."
    if len(pattern) > MAX_PATTERN_LEN:
        return f"That pattern is too long (limit {MAX_PATTERN_LEN} characters)."
    try:
        re.compile(pattern)
    except re.error as e:
        return f"Not a valid expression: {e}"
    if _BACKREF.search(pattern):
        return ("Back-references like \\1 are not allowed here - they can make "
                "the pattern run forever. Write the text out instead.")
    if _LOOKAROUND.search(pattern):
        return ("Look-ahead and look-behind ( (?= (?! (?<= (?<! ) are not "
                "allowed here. Use a simpler pattern, or the 'contains' type.")
    if sum(pattern.count(q) for q in "*+") > MAX_QUANTIFIERS:
        return (f"That pattern repeats too many times over "
                f"(limit {MAX_QUANTIFIERS} * or + symbols).")
    for body, quantified in _regex_groups(pattern):
        if not quantified:
            continue
        if any(q in body for q in "*+"):
            return ("That pattern repeats a group that already repeats "
                    "(something like (a+)+ ), which can make it run forever. "
                    "Usually the inner + or * can simply be removed.")
        if "|" in body:
            return ("That pattern repeats a group containing 'or' (a|b), which "
                    "can make it run forever. Repeat a single character class "
                    "like [ab]+ instead.")
    return None


@lru_cache(maxsize=512)
def safe_compile(pattern: str, case_sensitive: bool = False):
    """Compile ONLY if the gate accepts; None for a refused pattern.

    Every regex Auto Rename executes goes through here, so a pattern that
    reached the store by some route other than the editor - a hand-edited JSON
    file, an imported profile - still cannot run. Cached, so the gate costs
    nothing per file.
    """
    if screen_regex(pattern) is not None:
        return None
    try:
        return re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    except re.error:
        return None


# ---------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------

_TOKEN = re.compile(r"\{([a-z_]+)\}")


def render_template(template: str, values: dict) -> str:
    """Substitute {tokens} without str.format, so a stray brace in a staff
    member's template can never raise KeyError or reach __format__."""
    unknown = []

    def sub(m):
        key = m.group(1)
        if key not in values:
            unknown.append(key)
            return m.group(0)
        return values[key]

    out = _TOKEN.sub(sub, template)
    if unknown:
        raise KeyError(", ".join(sorted(set(unknown))))
    return out


# ---------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------

@lru_cache(maxsize=512)
def _word_pattern(needle: str):
    """Compile the whole-word matcher once per keyword.

    Rebuilt per file per rule, this was ~2,800 regex operations per keystroke
    on a 400-file folder with 7 keywords and a 60ms debounce. Python's internal
    pattern cache absorbed most of it, but the escape and lookup were still
    paid every time.
    """
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])")


@dataclass(frozen=True)
class KeywordRule:
    """One row of a profile's keyword -> ending table.

    ORDER IS SEMANTICS: evaluated top-down, FIRST match wins. So a
    'pet' row would also fire on 'Carpet'. Use match_mode
    'word' when that substring hazard matters.
    """
    keyword: str
    suffix: str
    match_mode: str = "contains"
    case_sensitive: bool = False
    enabled: bool = True

    def matches(self, name: str) -> bool:
        if not self.enabled or not self.keyword:
            return False
        hay = name if self.case_sensitive else name.lower()
        needle = self.keyword if self.case_sensitive else self.keyword.lower()
        mode = self.match_mode

        if mode == "contains":
            return needle in hay
        if mode == "not":
            return needle not in hay
        if mode == "word":
            return self._word_in(needle, hay)
        if mode == "any":
            return any(self._word_in(w.strip(), hay)
                       for w in needle.split(",") if w.strip())
        if mode == "starts":
            # After the ID and its separator, so "starts with" means what a
            # person means by it rather than "the first character of the file".
            return re.sub(r"^[0-9]+[\s._-]*", "", hay).startswith(needle)
        if mode == "ends":
            return os.path.splitext(hay)[0].rstrip().endswith(needle)
        if mode == "regex":
            rx = safe_compile(self.keyword, self.case_sensitive)
            return False if rx is None else rx.search(name) is not None
        return False

    @staticmethod
    def _word_in(needle: str, hay: str) -> bool:
        if not needle:
            return False
        return _word_pattern(needle).search(hay) is not None

    def validate(self):
        if self.match_mode not in MATCH_MODES:
            return f"Unknown match type {self.match_mode!r}"
        if not self.keyword:
            return "Keyword is empty"
        if self.match_mode == "any" and not [w for w in self.keyword.split(",") if w.strip()]:
            return "Put at least one word in, separated by commas"
        if self.match_mode == "regex":
            return screen_regex(self.keyword)
        return None


@dataclass
class RenameProfile:
    """A profile's complete renaming convention."""
    name: str
    id_pattern: str = r"^(\d+)"
    template: str = "{id}{suffix}{ext}"
    rules: list = field(default_factory=list)
    on_no_match: str = "skip"
    fallback_suffix: str = ""
    force_ext: str = ""
    ext_filter: list = field(default_factory=list)
    output_folder: str = "Renamed"   # "" = rename in place
    keep_originals: bool = True      # copy into the output folder, don't move
    lowercase_ext: bool = True       # ".PDF" -> ".pdf"
    description: str = ""
    builtin: bool = False
    locked: bool = False        # shared profile: read-only on this machine
    source: str = "local"       # "local" | "shared"

    # -- validation -----------------------------------------------------
    def validate(self) -> list:
        errs = []
        if not self.name.strip():
            errs.append("Profile name is empty")
        gate = screen_regex(self.id_pattern)
        if gate:
            errs.append(f"ID pattern: {gate}")
        elif re.compile(self.id_pattern).groups < 1:
            errs.append("Put brackets around the part that is the ID, "
                        "like ^(\\d+)")
        for tok in _TOKEN.findall(self.template):
            if tok not in TEMPLATE_FIELDS:
                errs.append(f"The new name uses an unknown field {{{tok}}} - "
                            f"you can use: " +
                            ", ".join("{%s}" % f for f in TEMPLATE_FIELDS))
        if self.on_no_match not in NO_MATCH_ACTIONS:
            errs.append(f"Unknown no-match action {self.on_no_match!r}")
        if self.output_folder:
            status, _note = _validate_basename(self.output_folder, "")
            if status != ST_OK or os.sep in self.output_folder or "/" in self.output_folder:
                errs.append("The folder for renamed files is not a usable folder "
                            "name. Use a plain name like 'Renamed'.")
        for i, r in enumerate(self.rules, 1):
            msg = r.validate()
            if msg:
                errs.append(f"Keyword {i} ({r.keyword!r}): {msg}")
        return errs

    @property
    def copies(self) -> bool:
        return bool(self.output_folder and self.keep_originals)

    # -- classification -------------------------------------------------
    def classify(self, filename: str):
        for r in self.rules:
            if r.matches(filename):
                return r.suffix, r.keyword
        return None, None

    def new_name(self, filename: str):
        """filename -> (new_name, status, note). Pure; touches no disk.

        status is ST_OK, ST_SKIPPED (benign - the file is left alone) or
        ST_INVALID (the profile itself is broken).
        """
        stem, ext = os.path.splitext(filename)

        if self.ext_filter and ext.lower() not in {e.lower() for e in self.ext_filter}:
            return "", ST_SKIPPED, "not one of this profile's file types"

        rx = safe_compile(self.id_pattern, True)
        if rx is None:
            return "", ST_INVALID, screen_regex(self.id_pattern) or "unusable ID pattern"
        if rx.groups < 1:
            # new_name() and validate() are independent entry points; a caller
            # that never validated must still not raise.
            return "", ST_INVALID, "put brackets around the part that is the ID"
        m = rx.match(filename)
        if not m:
            return "", ST_SKIPPED, "no ID found at the start of the name"
        file_id = m.group(1)

        suffix, keyword = self.classify(filename)
        if suffix is None:
            if self.on_no_match == "skip":
                return "", ST_SKIPPED, "no keyword matched"
            if self.on_no_match == "keep":
                return filename, ST_OK, ""
            suffix, keyword = self.fallback_suffix, ""

        out_ext = self.force_ext or ext
        if self.lowercase_ext:
            # Mixed-case extensions are the commonest thing wrong with a batch
            # of scans: the same document type arrives as .PDF, .Pdf and .pdf,
            # and every downstream sort and dedupe treats them as three things.
            # On by default; switch it off to keep the extension exactly as it
            # arrived.
            out_ext = out_ext.lower()

        try:
            built = render_template(self.template, {
                "id": file_id, "suffix": suffix, "ext": out_ext,
                "stem": stem, "orig": filename, "keyword": keyword or "",
            })
        except KeyError as e:
            return "", ST_INVALID, f"the new name uses an unknown field: {e}"
        return built, ST_OK, ""

    # -- serialisation --------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["rules"] = [asdict(r) for r in self.rules]
        return d

    @classmethod
    def from_dict(cls, d: dict):
        """Tolerant of a hand-edited file: unknown keys are dropped and a wrong
        type falls back to the default rather than raising."""
        if not isinstance(d, dict):
            raise ValueError("profile is not an object")
        rules = []
        for rd in d.get("rules") or []:
            if not isinstance(rd, dict):
                continue
            rules.append(KeywordRule(
                keyword=str(rd.get("keyword", "")),
                suffix=str(rd.get("suffix", "")),
                match_mode=(rd.get("match_mode")
                            if rd.get("match_mode") in MATCH_MODES else "contains"),
                case_sensitive=bool(rd.get("case_sensitive", False)),
                enabled=bool(rd.get("enabled", True)),
            ))
        ef = d.get("ext_filter")
        return cls(
            name=str(d.get("name", "")).strip() or "Unnamed",
            id_pattern=str(d.get("id_pattern") or r"^(\d+)"),
            template=str(d.get("template") or "{id}{suffix}{ext}"),
            rules=rules,
            on_no_match=(d.get("on_no_match")
                         if d.get("on_no_match") in NO_MATCH_ACTIONS else "skip"),
            fallback_suffix=str(d.get("fallback_suffix") or ""),
            force_ext=str(d.get("force_ext") or ""),
            ext_filter=[str(x) for x in ef] if isinstance(ef, list) else [],
            output_folder=("" if d.get("output_folder") == "" else
                           str(d.get("output_folder") or "Renamed")),
            keep_originals=bool(d.get("keep_originals", True)),
            lowercase_ext=bool(d.get("lowercase_ext", True)),
            description=str(d.get("description") or ""),
            builtin=bool(d.get("builtin", False)),
            locked=bool(d.get("locked", False)),
            source=("shared" if d.get("source") == "shared" else "local"),
        )

    def copy_as(self, new_name: str):
        """A copy is always local and always editable - that is the whole point
        of duplicating a locked shared profile."""
        return replace(self, name=new_name, builtin=False, locked=False,
                       source="local", rules=list(self.rules))

    # -- advisory warnings ----------------------------------------------
    def warnings(self) -> list:
        """Things that are legal but probably a mistake.

        Separate from validate(): these never block saving or renaming. They
        exist so the tool says the thing out loud BEFORE it costs somebody an
        afternoon, rather than being documented somewhere nobody reads.
        """
        out = []
        for i, r in enumerate(self.rules, 1):
            if not r.enabled or not r.keyword:
                continue
            if r.match_mode == "contains" and len(r.keyword) < 4:
                out.append(
                    f"Keyword {i} ('{r.keyword}') is short and set to 'contains', "
                    f"so it also matches inside longer words - 'pet' matches "
                    f"'Carpet'. Change it to 'contains the whole word' if that "
                    f"is not what you want.")
        seen = {}
        for i, r in enumerate(self.rules, 1):
            if not r.enabled or not r.keyword:
                continue
            key = (r.keyword.lower(), r.match_mode)
            if key in seen:
                out.append(f"Keyword {i} ('{r.keyword}') repeats keyword "
                           f"{seen[key]}. Only the first one can ever match.")
            else:
                seen[key] = i
        for i, r in enumerate(self.rules[:-1], 1):
            if r.enabled and r.match_mode == "not":
                out.append(f"Keyword {i} ('{r.keyword}') is a 'does NOT contain' "
                           f"rule but is not last. It will catch almost "
                           f"everything and the keywords below it will rarely "
                           f"be reached.")
        return out


def example_profile() -> RenameProfile:
    """A small starter table so a new install has something to read and edit.

    The words and endings are placeholders. Order is semantics (first match
    wins), so replace them with the words that really appear in your file
    names before using it on real files.
    """
    return RenameProfile(
        name="Example",
        rules=[
            KeywordRule("invoice", "-Invoice"),
            KeywordRule("receipt", "-Receipt"),
            KeywordRule("contract", "-Contract"),
            KeywordRule("report", "-Report"),
        ],
        description=("A starter example. Edit the keywords to match how your "
                     "files are named. A file with no keyword match is skipped."),
        builtin=True,
    )


def blank_profile(name: str) -> RenameProfile:
    return RenameProfile(
        name=name,
        rules=[],
        description=(f"{name} has no keywords yet. Add the rows that match how "
                     f"{name} documents actually arrive, then try a real "
                     f"filename below before using it."),
    )


def seed_profiles() -> list:
    """One editable example. Real tables are created by the user: inventing
    keyword tables for sources nobody has verified would produce confident
    wrong renames."""
    return [example_profile()]


# ---------------------------------------------------------------------
# Storage — a plain JSON file beside the presets, same conventions
# ---------------------------------------------------------------------

def profiles_path() -> str:
    return os.path.join(_log_dir(), "profiles.json")


def shared_profiles_path() -> str:
    """The shared, read-only profile file, if one is configured.

    WHY THIS EXISTS: without it every workstation keeps its own
    profiles.json and they drift the moment one person corrects a rule.
    Nobody notices until two people rename the same folder differently. Point
    every machine at one file on the share and a fix reaches everyone by
    replacing that file.
    """
    return str(load_ui_state().get("shared_profiles_path") or "")


def set_shared_profiles_path(path: str) -> None:
    state = load_ui_state()
    state["shared_profiles_path"] = path or ""
    save_ui_state(state)


def _read_profiles_file(path: str):
    """(profiles, error). Never raises - a bad shared file must not stop the
    app starting, it must say so and carry on with what it has."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        items = raw.get("profiles") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise ValueError("no profiles list")
        out = []
        for it in items:
            try:
                out.append(RenameProfile.from_dict(it))
            except Exception:
                continue            # one bad row never loses the rest
        return out, ""
    except Exception as e:
        return [], str(e)


def load_profiles():
    """Returns (profiles, notes). Shared profiles first, then local ones.

    SHARED profiles are locked on this machine: they are changed by replacing
    the shared file, not from the app. LOCAL profiles are this machine's own.

    A local profile whose name collides with a shared one is RENAMED, never
    dropped. Silently discarding somebody's work to resolve a name clash is the
    one outcome this function must never produce, so the collision is loud and
    the work survives.
    """
    notes = []

    shared_set = []
    cpath = shared_profiles_path()
    if cpath:
        if not os.path.exists(cpath):
            notes.append(f"The shared profile file was not found at {cpath}. "
                         f"Using this machine's own profiles only.")
        else:
            shared_set, err = _read_profiles_file(cpath)
            if err:
                notes.append(f"The shared profile file could not be read ({err}). "
                             f"Using this machine's own profiles only.")
            for p in shared_set:
                p.locked = True
                p.source = "shared"

    local = []
    lpath = profiles_path()
    if os.path.exists(lpath):
        local, lerr = _read_profiles_file(lpath)
        if lerr:
            notes.append(f"This machine's profile file could not be read ({lerr}). "
                         f"The built-in profiles were loaded instead.")
        for p in local:
            p.locked = False
            p.source = "local"

    if not shared_set and not local:
        return seed_profiles(), notes

    taken = {p.name for p in shared_set}
    merged = list(shared_set)
    for p in local:
        if p.name in taken:
            new_name = unique_profile_name(f"{p.name} (this machine)", taken)
            notes.append(f"'{p.name}' is also in the shared profile file, so this "
                         f"machine's version was renamed to '{new_name}'. "
                         f"Nothing was deleted.")
            p.name = new_name
        taken.add(p.name)
        merged.append(p)
    return merged, notes


def save_profiles(profiles) -> None:
    """Writes ONLY the local profiles.

    A shared profile lives in the shared file and is never written back from a
    workstation - that is exactly what makes it one source of truth instead of
    N drifting copies.
    """
    payload = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "app_version": APP_VERSION,
        "profiles": [p.to_dict() for p in profiles if p.source != "shared"],
    }
    _atomic_write_text(profiles_path(),
                       json.dumps(payload, indent=2, ensure_ascii=False))


def export_profile(profile, dest: str) -> None:
    _atomic_write_text(dest, json.dumps(
        {"schema_version": PROFILE_SCHEMA_VERSION, "profiles": [profile.to_dict()]},
        indent=2, ensure_ascii=False))


def import_profiles(src: str) -> list:
    with open(src, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    items = raw.get("profiles") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("That file does not contain any profiles.")
    out = []
    for it in items:
        p = RenameProfile.from_dict(it)
        p.builtin = False
        out.append(p)
    return out


def _atomic_write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def unique_profile_name(base: str, existing) -> str:
    names = set(existing)
    if base not in names:
        return base
    i = 2
    while f"{base} ({i})" in names:
        i += 1
    return f"{base} ({i})"


# ---------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------

def build_profile_plan(paths, profile: RenameProfile) -> RenamePlan:
    """Classify every file and hand the names to the shared plan core.

    Renamed files go into a sub-folder of the folder they came from
    (profile.output_folder, "Renamed" by default), and by default the original
    is COPIED rather than moved - the originals are left exactly as they are.
    """
    errs = profile.validate()
    if errs:
        return RenamePlan([
            RenameRow(src=os.path.abspath(p), dst="", status=ST_INVALID, note=errs[0])
            for p in paths])

    ordered = sorted(paths, key=lambda p: (os.path.dirname(p).casefold(),
                                           os.path.basename(p).casefold()))
    entries = []
    matched = []
    for src in ordered:
        src = os.path.abspath(src)
        folder, base = os.path.split(src)
        out_folder = (os.path.join(folder, profile.output_folder)
                      if profile.output_folder else folder)
        new_base, status, note = profile.new_name(base)
        if status != ST_OK:
            entries.append((src, folder, "", out_folder, status, note))
        else:
            # Carry WHICH keyword matched through to the preview. The engine
            # already worked it out and used to throw it away, leaving six
            # files reading "-SPS" with no way to tell whether that was 'coun'
            # or 'city' without re-reading the rule list.
            _suffix, keyword = profile.classify(base)
            matched.append((src, f"matched '{keyword}'" if keyword else ""))
            entries.append((src, folder, new_base, out_folder, None, ""))

    # Nothing is ever renamed out of the way unless this is a true in-place
    # rename: with an output folder the source stays put, and when the
    # originals are kept it is a copy, so it definitely never moves.
    movable = frozenset()
    if not profile.output_folder and not profile.keep_originals:
        movable = {os.path.abspath(p).casefold() for p in ordered}
    plan = _assemble_plan(entries, movable=movable, copies=profile.copies)
    why = dict(matched)
    for row in plan.rows:
        if row.status == ST_OK and not row.note and row.src in why:
            row.note = why[row.src]
    return plan


# ---------------------------------------------------------------------

PRESET_SCHEMA_VERSION = 1


def ui_state_path() -> str:
    return os.path.join(_log_dir(), "ui_state.json")


# ui_state.json is read on many hot paths (theme apply, every splitter drag
# sample, every Settings toggle). One in-process copy, keyed by path so a
# relocated settings dir (tests, a future "move settings" option) is never
# served a stale blob. The single-instance QLockFile in main() is what makes
# an in-process cache safe: no other writer exists while we run.
_UI_STATE_CACHE = {}


def _reset_ui_state_cache() -> None:
    _UI_STATE_CACHE.clear()


def load_ui_state() -> dict:
    """A COPY of the current state. Mutate it, then save_ui_state()."""
    path = ui_state_path()
    cached = _UI_STATE_CACHE.get(path)
    if cached is None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                blob = json.load(f)
            cached = blob if isinstance(blob, dict) else {}
        except (OSError, json.JSONDecodeError):
            cached = {}
        _UI_STATE_CACHE[path] = cached
    return json.loads(json.dumps(cached)) if cached else {}


def save_ui_state(state: dict) -> None:
    path = ui_state_path()
    _UI_STATE_CACHE[path] = json.loads(json.dumps(state))
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def update_ui_state(**changes) -> dict:
    """Read-modify-write in one call. Returns the new state."""
    state = load_ui_state()
    state.update(changes)
    save_ui_state(state)
    return state


RECENT_MAX = 8


_OLD_ORGANIZED_DIR_NAME = "Organized"


def _valid_setting(key, value):
    if key in ("split_dir_name", "optimized_dir_name", "organized_dir_name",
               "cover_suffix", "remaining_suffix"):
        if key == "organized_dir_name" and isinstance(value, str) \
                and value.strip().lower() == "originals":
            return False
        return valid_name_component(value)
    is_int = isinstance(value, int) and not isinstance(value, bool)
    if key in ("optimizer_allow_flatten", "optimizer_open_when_done", "mascot_dialogs",
               "organizer_replace_original", "organizer_extract_delete_default", "organizer_keep_scripts",
               "actions_collapsed", "launch_reader_full"):
        return isinstance(value, bool)
    if key == "actions_side":
        return value in ("left", "right")
    if key == "optimizer_flatten_dpi":
        return is_int and 72 <= value <= 300
    if key == "organizer_thumb_size":
        return is_int and 100 <= value <= 260
    if key == "organizer_undo_depth":
        return is_int and 5 <= value <= 200
    if key == "organizer_auto_refresh":
        return value in ("never", "focus", "timer")
    if key == "ui_layout":
        return value in ("tray", "classic")
    if key == "reader_default_zoom":
        return value in ("width", "page", "100")
    if key == "reader_cache_mb":
        return is_int and 64 <= value <= 1024
    if key == "merge_backups":
        return is_int and 1 <= value <= 10
    if key == "undo_depth":
        return is_int and 1 <= value <= 20
    if key == "default_tab":
        return is_int and -1 <= value <= 20
    if key == "mover_separators":
        return isinstance(value, str) and not any(c.isalnum() for c in value)
    if key == "ceiling_presets":
        return (isinstance(value, list) and all(
            isinstance(r, list) and len(r) == 2 and isinstance(r[0], str) and r[0].strip()
            and isinstance(r[1], (int, float)) and 0 < r[1] <= 2000 for r in value))
    return False


def _dict_at(state: dict, key: str) -> dict:
    """state[key] as a dict, replacing anything else (a hand-edited file)."""
    value = state.get(key)
    if not isinstance(value, dict):
        value = {}
        state[key] = value
    return value


def setting(key):
    """An Advanced setting, validated, or its default."""
    default = ADVANCED_DEFAULTS[key]
    adv = load_ui_state().get("advanced")
    value = adv.get(key, default) if isinstance(adv, dict) else default
    if key == "organized_dir_name" and value == _OLD_ORGANIZED_DIR_NAME:
        return default             # the folder was called "Organized" before 1.4.3; saved copies of that default follow the rename
    return value if _valid_setting(key, value) else default


def save_settings(changes: dict) -> list:
    """Validate and persist. Returns the keys that were REJECTED (unchanged)."""
    rejected = [k for k, v in changes.items()
                if k not in ADVANCED_DEFAULTS or not _valid_setting(k, v)]
    state = load_ui_state()
    adv = _dict_at(state, "advanced")
    for k, v in changes.items():
        if k not in rejected:
            adv[k] = v
    save_ui_state(state)
    if "undo_depth" in changes and "undo_depth" not in rejected:
        # A smaller depth takes effect now, not after the next run.
        for path in (undo_log_path(), move_log_path()):
            prune_undo_history(path, changes["undo_depth"])
    return rejected


def recent_paths(key: str) -> list:
    """Most-recent-first list for a Recent menu. NOT existence-checked: a
    stat() on an offline network share can block for a minute, and this
    runs on a click. A dead entry is dropped by forget_recent() when
    choosing it fails."""
    recent = load_ui_state().get("recent")
    items = recent.get(key, []) if isinstance(recent, dict) else []
    return [p for p in items if isinstance(p, str) and p] if isinstance(items, list) else []


def push_recent(key: str, path: str) -> None:
    if not path:
        return
    state = load_ui_state()
    recent = _dict_at(state, "recent")
    items = recent.get(key, [])
    items = [p for p in items if p != path] if isinstance(items, list) else []
    recent[key] = ([path] + items)[:RECENT_MAX]
    save_ui_state(state)


def forget_recent(key: str, path: str) -> None:
    state = load_ui_state()
    recent = _dict_at(state, "recent")
    items = recent.get(key, [])
    if isinstance(items, list) and path in items:
        recent[key] = [p for p in items if p != path]
        save_ui_state(state)


# Every path the app remembers between runs, as opposed to a setting. All of
# it is cleared when the window closes (see clear_recent_paths).
REMEMBERED_PATH_KEYS = ("merger_last_reference", "splitter_last_dir", "renamer_last_folder",
                        "mover_last_source", "mover_last_dest", "organizer_last_folder")


def clear_recent_paths() -> None:
    """Forget every Recent list and every remembered last folder / file.
    Window geometry, the open tab, presets and settings are untouched."""
    state = load_ui_state()
    changed = state.pop("recent", None) is not None
    for key in REMEMBERED_PATH_KEYS:
        if key in state:
            del state[key]
            changed = True
    if changed:
        save_ui_state(state)


def presets_path() -> str:
    return os.path.join(_log_dir(), "rename_presets.json")


def load_presets() -> dict:
    path = presets_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(blob, dict) or blob.get("version") != PRESET_SCHEMA_VERSION:
        return {}
    out = {}
    valid = set(RenameRule().__dict__.keys())
    for name, data in (blob.get("presets") or {}).items():
        if isinstance(data, dict):
            out[name] = RenameRule(**{k: v for k, v in data.items() if k in valid})
    return out


def save_presets(presets: dict) -> None:
    blob = {
        "version": PRESET_SCHEMA_VERSION,
        "presets": {name: asdict(rule) for name, rule in presets.items()},
    }
    path = presets_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------
# Windows title bar
#
# Windows draws the caption itself; a Qt stylesheet cannot reach it, which is
# why a dark-themed app can sit under a white title bar. DWM exposes it
# through DwmSetWindowAttribute — ctypes again, no new dependency.
#
# What is NOT done here: Mica / Acrylic backdrops. Those require the profile
# area to be translucent so the material shows through, which fights every
# opaque background this app paints. Matching the caption colour is the whole
# win; a backdrop would be a redesign.
# ---------------------------------------------------------------------

DWMWA_USE_IMMERSIVE_DARK_MODE = 20   # Windows 10 1809+
DWMWA_BORDER_COLOR = 34              # Windows 11 build 22000+
DWMWA_CAPTION_COLOR = 35             # Windows 11 build 22000+
DWMWA_TEXT_COLOR = 36                # Windows 11 build 22000+


# ==== acrod_core END ==================================================


class _IoProbe(QObject):
    """Run a filesystem call on a daemon thread and deliver the result on
    the UI thread. For the probes that can HANG - stat()/listdir() on an
    offline network share - a QThread is the wrong tool: a QThread still
    blocked at exit crashes the process, a daemon thread is simply dropped.

    Usage: _IoProbe(self, fn, on_done).start(). `on_done(result_or_exc)` is a
    bound method of a QObject on the UI thread; `fn` touches no widget."""

    finished = Signal(object)

    def __init__(self, parent, fn, on_done):
        super().__init__(parent)
        self._fn = fn
        self.finished.connect(on_done)
        self.finished.connect(self.deleteLater)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            result = self._fn()
        except Exception as exc:          # noqa: BLE001 - delivered, not swallowed
            result = exc
        self.finished.emit(result)


# Files the program will not hand to Windows to RUN. The Bulk Renamer lists
# whatever is in a folder, and "Open file" on an unknown download should show
# it, not start it. Documents and images open as before.
_RUNNABLE_EXT = frozenset((
    ".exe", ".com", ".scr", ".bat", ".cmd", ".msi", ".msp", ".ps1", ".psm1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".url", ".jar", ".reg", ".dll", ".cpl",
    ".pif", ".gadget", ".application", ".appref-ms", ".docm", ".xlsm", ".pptm"))


def open_file(path: str) -> bool:
    """Open a file in its normal program, unless it is a kind that runs code:
    those are shown in their folder instead. Returns True if it was opened."""
    if os.path.splitext(path)[1].lower() in _RUNNABLE_EXT:
        open_in_explorer(path)
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def open_in_explorer(path: str) -> bool:
    """Open a folder, or the folder holding a file. False if it is gone."""
    if not path:
        return False
    target = path if os.path.isdir(path) else os.path.dirname(path)
    if not os.path.isdir(target):
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(target))


def _colorref(hex_colour: str) -> int:
    """Windows wants 0x00BBGGRR, not RGB."""
    h = hex_colour.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_titlebar_theme(window, palette: dict) -> bool:
    """Match the Windows caption to the active theme. Returns True if applied.

    Every call is best-effort: an older Windows build simply refuses the
    unsupported attributes and keeps its default caption, which is a correct
    outcome rather than a failure. Never raises.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        dwm = ctypes.WinDLL("dwmapi")
    except Exception:
        return False

    try:
        hwnd = wintypes.HWND(int(window.winId()))
    except Exception:
        return False

    def set_attr(attr: int, value: int) -> bool:
        try:
            v = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(
                hwnd, ctypes.c_uint(attr), ctypes.byref(v), ctypes.sizeof(v)) == 0
        except Exception:
            return False

    # Luminance of the window ground decides light vs dark caption buttons.
    h = palette["bg"].lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    is_dark = (0.2126 * r + 0.7152 * g + 0.0722 * b) < 0.35

    ok = set_attr(DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if is_dark else 0)
    if not ok:
        # Windows 10 builds 1809-1903 use attribute 19 for the same thing;
        # 20 is simply rejected there, which left a dark app under a white
        # caption with no fallback.
        ok = set_attr(19, 1 if is_dark else 0)
    # Windows 11 only — these fail harmlessly on Windows 10.
    set_attr(DWMWA_CAPTION_COLOR, _colorref(palette["bg"]))
    set_attr(DWMWA_TEXT_COLOR, _colorref(palette["text"]))
    set_attr(DWMWA_BORDER_COLOR, _colorref(palette["border"]))
    return ok


# ---------------------------------------------------------------------
# File Locksmith — who is holding this file?
#
# Windows only, via the Restart Manager API in rstrtmgr.dll (the same API
# PowerToys' File Locksmith uses). ctypes only: no new dependency, no import
# cost at start-up. Everywhere else it returns an empty list, so callers need
# no platform branch of their own.
#
# Deliberately NOT a pre-check. Testing 400 files before a batch means 400
# API round trips AND a race — a file can be opened between the check and the
# rename. It runs only after an operation has actually failed.
# ---------------------------------------------------------------------

RM_SESSION_KEY_LEN = 32  # CCH_RM_SESSION_KEY + 1, as UTF-16
_RM_APP_TYPES = {
    0: "unknown program", 1: "a program", 2: "a Windows service",
    3: "Windows Explorer", 4: "a system service", 5: "a background task",
}


def who_locks(path: str) -> list:
    """Return human-readable descriptions of processes holding `path`.

    Empty list means nothing found, not-Windows, or the API refused — callers
    treat all three the same way, which keeps this advisory and unable to make
    a failure worse.
    """
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []

    try:
        rm = ctypes.WinDLL("rstrtmgr")
    except Exception:
        return []

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD),
                    ("ProcessStartTime", wintypes.FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [
            ("Process", RM_UNIQUE_PROCESS),
            ("strAppName", wintypes.WCHAR * 256),
            ("strServiceShortName", wintypes.WCHAR * 64),
            ("ApplicationType", ctypes.c_int),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    session = wintypes.DWORD()
    key = (wintypes.WCHAR * RM_SESSION_KEY_LEN)()
    if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []

    try:
        resources = (ctypes.c_wchar_p * 1)(ctypes.c_wchar_p(os.path.abspath(path)))
        if rm.RmRegisterResources(session, 1, resources, 0, None, 0, None) != 0:
            return []

        needed = ctypes.c_uint(0)
        count = ctypes.c_uint(0)
        reason = wintypes.DWORD()
        # First call sizes the buffer; ERROR_MORE_DATA (234) is the expected
        # result and is not a failure.
        rc = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                          None, ctypes.byref(reason))
        if needed.value == 0:
            return []
        infos = (RM_PROCESS_INFO * needed.value)()
        count = ctypes.c_uint(needed.value)
        rc = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                          infos, ctypes.byref(reason))
        if rc != 0:
            return []

        out = []
        for i in range(count.value):
            info = infos[i]
            name = (info.strAppName or info.strServiceShortName or "").strip()
            if not name:
                name = _RM_APP_TYPES.get(info.ApplicationType, "a program")
            out.append(f"{name} (PID {info.Process.dwProcessId})")
        return out
    except Exception:
        return []
    finally:
        try:
            rm.RmEndSession(session)
        except Exception:
            pass


def human_size(n: int) -> str:
    n = max(0, int(n))
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024.0
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if n < 100 or unit == "GB" else f"{n:.0f} {unit}"


def classify_drop(paths):
    """What a drop (or a file dialog) actually contains, for every tab that takes PDFs:
    {"pdfs": [...], "skipped": [(name, plain reason)], "bytes": total size of the PDFs}.
    Cheap: names, a stat and the first KB. Nothing is opened."""
    pdfs, skipped, total = [], [], 0
    for p in paths:
        name = os.path.basename(p.rstrip("/\\")) or p
        if os.path.isdir(p):
            skipped.append((name, "is a folder, not a PDF"))
        elif not os.path.exists(p):
            skipped.append((name, "was not found"))
        elif not p.lower().endswith(".pdf"):
            skipped.append((name, "is not a PDF"))
        else:
            problem = pdf_file_problem(p)
            if problem:
                skipped.append((name, "is not a usable PDF (too large or damaged)"
                                if "too large" in problem else "does not look like a PDF inside"))
                continue
            pdfs.append(p)
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
    return {"pdfs": pdfs, "skipped": skipped, "bytes": total}


def describe_drop(info) -> str:
    """'3 PDFs, 4.2 MB. Skipped: a.docx (is not a PDF), notes (is a folder, not a PDF).'"""
    n = len(info["pdfs"])
    text = f"{plural(n, 'PDF')}, {human_size(info['bytes'])}" if n else "No PDFs"
    if info["skipped"]:
        shown = ", ".join(f"{nm} ({why})" for nm, why in info["skipped"][:5])
        more = len(info["skipped"]) - 5
        text += f". Skipped: {shown}" + (f" and {more} more" if more > 0 else "")
    return text + "."


ERROR_LOG_MAX = 256 * 1024


def log_error_detail(exc: BaseException, context: str = "") -> None:
    """Keep the technical detail of a failure in a small log, so the message
    shown to the person can stay in plain words. Never raises."""
    try:
        import traceback
        path = os.path.join(_log_dir(), "errors.log")
        try:
            if os.path.getsize(path) > ERROR_LOG_MAX:
                os.replace(path, path + ".old")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {context}\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:])
    except Exception:
        pass


def plain_error(exc: BaseException, path: str = "") -> str:
    """One sentence a person can act on for an error nobody planned for. The raw
    exception text goes to the log, not the dialog."""
    import errno as _errno
    name = os.path.basename(path) if path else ""
    who = f"'{name}'" if name else "That file"
    log_error_detail(exc, name)
    if isinstance(exc, MemoryError):
        return f"{who} is too big to process with the memory this computer has free. Close other programs and try again."
    if isinstance(exc, FileNotFoundError):
        return f"{who} could not be found. It may have been moved, renamed or deleted."
    if isinstance(exc, PermissionError):
        return (f"{who} could not be changed: Windows refused access. It may be open in another program, "
                "read-only, or in a folder you cannot write to.")
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) == _errno.ENOSPC:
            return "The disk is full. Free some space and try again."
        if getattr(exc, "errno", None) == _errno.ENAMETOOLONG:
            return f"{who} has a path that is too long for Windows."
        return f"{who} could not be read or written (a disk or network problem). Try again, or copy it to this computer first."
    kind = type(exc).__name__
    if kind.startswith("Pdf") or "pypdf" in type(exc).__module__ or "pdfium" in type(exc).__module__:
        return f"{who} could not be read as a PDF. It may be damaged or not really a PDF."
    return (f"{who} could not be finished because of an unexpected problem ({kind}). "
            "Nothing was changed. The details were saved in the error log.")


def describe_lock_failure(path: str, error: OSError) -> str:
    """Turn a raw OSError into something a person can act on."""
    base = os.path.basename(path)
    holders = who_locks(path)
    if holders:
        who = holders[0] if len(holders) == 1 else ", ".join(holders)
        return f"{base} is open in {who}. Close it and try again."
    if getattr(error, "winerror", None) == 32 or "used by another process" in str(error):
        return (f"{base} is open in another program. Close it and try again. "
                f"(Could not identify which program.)")
    return plain_error(error, path)


# ---------------------------------------------------------------------
# Peek — what is actually in this file?
#
# Text only. pypdf already ships with the app and extracts page-1 text in
# ~2ms, which yields the title and reference number — the things that
# answer "am I renaming the right file?". A rendered thumbnail would need
# pypdfium2/PyMuPDF: a new dependency and more cold-start on top of the ~5s
# Qt import. Not worth it for less useful information.
# ---------------------------------------------------------------------

PEEK_MAX_LINES = 6
PEEK_MAX_CHARS = 400


def peek_file(path: str) -> dict:
    """Cheap, read-only description of a file. Never raises."""
    out = {"name": os.path.basename(path), "size": None, "modified": None,
           "pages": None, "encrypted": None, "text": [], "error": ""}
    try:
        st = os.stat(path)
        out["size"] = st.st_size
        out["modified"] = st.st_mtime
    except OSError as e:
        out["error"] = f"Could not read this file: {e}"
        return out

    if not path.lower().endswith(".pdf"):
        return out

    try:
        if pdf_expansion_problem(path):
            out["error"] = "Not previewed: this PDF is built to expand to an enormous size."
            return out
        PdfReader, _ = _pypdf()
        reader = PdfReader(path)
        out["encrypted"] = reader.is_encrypted
        if reader.is_encrypted:
            out["error"] = "Password-protected — contents cannot be shown."
            return out
        out["pages"] = len(reader.pages)
        if reader.pages:
            raw = reader.pages[0].extract_text() or ""
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            out["text"] = lines[:PEEK_MAX_LINES]
            if not lines:
                out["error"] = "No text on page 1 — this may be a scan."
    except Exception as e:
        out["error"] = f"Could not read the PDF: {e}"
    return out


def format_peek(info: dict) -> str:
    """One short block of text for the peek pane."""
    bits = []
    if info["size"] is not None:
        kb = info["size"] / 1024
        bits.append(f"{kb:,.0f} KB" if kb >= 1 else f"{info['size']} bytes")
    if info["pages"] is not None:
        bits.append(f"{info['pages']} page" + ("s" if info["pages"] != 1 else ""))
    if info["encrypted"]:
        bits.append("password-protected")
    head = f"{info['name']}" + (f"   ·   {'  ·  '.join(bits)}" if bits else "")

    body = []
    if info["error"]:
        body.append(info["error"])
    for ln in info["text"]:
        body.append(ln[:PEEK_MAX_CHARS])
    return head + ("\n" + "\n".join(body) if body else "")


def export_presets(dest_path: str) -> int:
    """Write the preset library to a file the user chose. Returns the count."""
    presets = load_presets()
    if not presets:
        raise RenameExecError("There are no presets to export yet.")
    blob = {
        "kind": "acrod-rename-presets",
        "version": PRESET_SCHEMA_VERSION,
        "app_version": APP_VERSION,
        "presets": {name: asdict(rule) for name, rule in presets.items()},
    }
    tmp = dest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, indent=2)
    os.replace(tmp, dest_path)
    return len(presets)


def import_presets(src_path: str, overwrite: bool = False):
    """Merge a preset file into the library. Returns (added, skipped, renamed).

    Never silently overwrites: a name that already exists is either skipped or,
    when overwrite is off, imported under a suffixed name. A preset is somebody's
    saved rule — losing one to a filename collision is not an acceptable default.
    """
    try:
        with open(src_path, "r", encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RenameExecError(f"That file could not be read as a preset file: {e}") from e

    if not isinstance(blob, dict) or blob.get("kind") != "acrod-rename-presets":
        raise RenameExecError(
            "That does not look like an Acro-Data preset file."
        )
    if blob.get("version") != PRESET_SCHEMA_VERSION:
        raise RenameExecError(
            f"That preset file was written by a different version "
            f"(file: {blob.get('version')}, this app: {PRESET_SCHEMA_VERSION})."
        )

    incoming = blob.get("presets")
    if not isinstance(incoming, dict) or not incoming:
        raise RenameExecError("That preset file contains no presets.")

    valid = set(RenameRule().__dict__.keys())
    existing = load_presets()
    added, skipped, renamed = 0, 0, []

    for name, data in incoming.items():
        if not isinstance(name, str) or not isinstance(data, dict):
            skipped += 1
            continue
        rule = RenameRule(**{k: v for k, v in data.items() if k in valid})
        if name in existing:
            if overwrite:
                existing[name] = rule
                added += 1
            else:
                new_name = f"{name} (imported)"
                n = 2
                while new_name in existing:
                    new_name = f"{name} (imported {n})"
                    n += 1
                existing[new_name] = rule
                renamed.append(new_name)
                added += 1
        else:
            existing[name] = rule
            added += 1

    save_presets(existing)
    return added, skipped, renamed


def find_orphan_temps(folder: str, recurse: bool) -> list:
    """Leftover staging files from a rename that was killed between phases.
    collect_files hides them, so without this they are invisible."""
    found = []
    try:
        if recurse:
            for root, _d, files in os.walk(folder):
                found.extend(os.path.join(root, n) for n in files if is_temp_name(n))
        else:
            found.extend(os.path.join(folder, n) for n in os.listdir(folder)
                         if is_temp_name(n) and os.path.isfile(os.path.join(folder, n)))
    except OSError:
        pass
    return found


def collect_files(folder: str, recurse: bool, ext_filter: str = "") -> list:
    """Files only — directories are never returned, never renamed."""
    exts = [
        e.strip().lower().lstrip("*").lstrip(".")
        for e in ext_filter.split(",") if e.strip()
    ]

    def keep(name):
        if not exts:
            return True
        return os.path.splitext(name)[1].lower().lstrip(".") in exts

    found = []
    if recurse:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if keep(name) and not is_temp_name(name):
                    found.append(os.path.join(root, name))
    else:
        try:
            for name in sorted(os.listdir(folder)):
                full = os.path.join(folder, name)
                if os.path.isfile(full) and keep(name) and not is_temp_name(name):
                    found.append(full)
        except OSError:
            return []
    return found


# =====================================================================
# Widgets
# =====================================================================

class _BatchWorker(QThread):
    """
    Run fn(item) for each item, off the UI thread. The generic sibling of
    _OptimizeWorker for the tabs whose work is one call per file (merge,
    split, move). Replaces the processEvents() pattern those tabs used: a
    single large merge or a 400-file move froze the window for its whole
    duration with no way to stop it.

    Cancel is honoured BETWEEN items - the pure ops are atomic per file
    (temp + os.replace), so stopping between them never leaves a half
    result. The item in flight when Cancel is pressed still completes.
    """

    item_started = Signal(int, object)          # index, item
    item_done = Signal(int, object, object)     # index, item, result
    item_failed = Signal(int, object, str)      # index, item, plain-language message
    finished_all = Signal(bool)                 # True if cancelled
    progress = Signal(int, int)                 # done, total - for fns that report within an item

    def __init__(self, items, fn, parent=None, path_of=None):
        super().__init__(parent)
        self._items = list(items)
        self._fn = fn
        self._path_of = path_of          # None: items are not paths
        self._cancel = False
        # Parenting keeps the thread alive past `panel._worker = None`;
        # deleteLater keeps a long session from accumulating one dead QThread
        # per run.
        self.finished.connect(self.deleteLater)

    def _name(self, item) -> str:
        if self._path_of is None:
            return ""
        return os.path.basename(self._path_of(item))

    def cancel(self):
        self._cancel = True

    def is_cancelled(self):
        return self._cancel

    def run(self):
        for i, item in enumerate(self._items):
            if self._cancel:
                break
            self.item_started.emit(i, item)
            try:
                result = self._fn(item)
            except (PdfOpError, RenameExecError, RenameRuleError) as exc:
                self.item_failed.emit(i, item, str(exc))
            except OSError as exc:
                self.item_failed.emit(
                    i, item,
                    describe_lock_failure(self._path_of(item), exc)
                    if self._path_of is not None else str(exc))
            except Exception as exc:
                self.item_failed.emit(
                    i, item,
                    plain_error(exc, self._path_of(item) if self._path_of is not None else ""))
            else:
                self.item_done.emit(i, item, result)
        self.finished_all.emit(self._cancel)


_INSPECTORS = weakref.WeakSet()
_NARROW_BELOW = 940                        # px of work width under which the Actions column folds away by itself
_AUTO = {"narrow": False, "open": False}   # session only: the window is narrow / the user opened the column anyway
_ACTIONS_LISTENERS = weakref.WeakSet()    # Settings panels showing the side / collapsed controls
_RAIL_WIDTH = 40


class InspectorFrame(QFrame):
    """The Actions column of a tool: a header with a collapse button over a
    body that holds the tool's buttons. Which side it sits on and whether it
    is collapsed come from the settings, so every tool agrees and a change
    applies at once."""

    def __init__(self, shell, left, title, width):
        super().__init__()
        self.setObjectName("inspector")
        self._shell = shell
        self._left = left
        self._open_width = width
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 20, 10, 0)
        hl.setSpacing(6)
        self.cap = QLabel(title.upper())
        self.cap.setObjectName("sectionTitle")
        self.toggle_btn = QPushButton()
        self.toggle_btn.setObjectName("railToggle")
        self.toggle_btn.setIconSize(QSize(20, 20))
        self.toggle_btn.setFixedSize(32, 30)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.setFocusPolicy(Qt.TabFocus)
        self.toggle_btn.setAccessibleName(f"Hide or show the {title} column")
        self.toggle_btn.clicked.connect(inspector_toggle)
        hl.addWidget(self.cap, 1)
        hl.addWidget(self.toggle_btn, 0, Qt.AlignTop)      # collapsed, the head fills the rail: keep the button at the top
        self.head = head
        outer.addWidget(head)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(20, 14, 20, 16)
        self.body_layout.setSpacing(10)
        outer.addWidget(self.body, 1)
        _INSPECTORS.add(self)
        self.apply_prefs()

    def apply_prefs(self):
        side = setting("actions_side")
        collapsed = bool(setting("actions_collapsed")) or (_AUTO["narrow"] and not _AUTO["open"])
        self.setProperty("side", side)
        self._shell.removeWidget(self._left)
        self._shell.removeWidget(self)
        if side == "left":
            self._shell.addWidget(self)
            self._shell.addWidget(self._left, 1)
        else:
            self._shell.addWidget(self._left, 1)
            self._shell.addWidget(self)
        self.body.setVisible(not collapsed)
        self.cap.setVisible(not collapsed)
        hl = self.head.layout()
        if collapsed:
            self.setFixedWidth(_RAIL_WIDTH)
            hl.setContentsMargins(4, 14, 4, 0)
            self.toggle_btn.setIcon(panel_toggle_icon(side, False))
            self.toggle_btn.setToolTip("Show the Actions column (Ctrl+J)")
        else:
            self.setFixedWidth(self._open_width)
            hl.setContentsMargins(20, 20, 10, 0)
            self.toggle_btn.setIcon(panel_toggle_icon(side, True))
            self.toggle_btn.setToolTip("Hide the Actions column (Ctrl+J)")
        st = self.style()
        st.unpolish(self)
        st.polish(self)


def refresh_inspectors() -> None:
    """Re-read the side / collapsed settings into every Actions column that exists."""
    for insp in list(_INSPECTORS):
        try:
            insp.apply_prefs()
        except RuntimeError:        # its tool was closed and the C++ side is gone
            pass
    for panel in list(_ACTIONS_LISTENERS):
        try:
            panel.sync_actions_controls()
        except RuntimeError:
            pass


def inspector_toggle(*_args) -> None:
    """The rail button / Ctrl+J. In a narrow window the column is folded for lack of room, so this only
    opens or closes it for now; otherwise it flips the saved setting."""
    if _AUTO["narrow"] and not setting("actions_collapsed"):
        _AUTO["open"] = not _AUTO["open"]
    else:
        save_settings({"actions_collapsed": not setting("actions_collapsed")})
    refresh_inspectors()


def set_actions_room(width: int) -> None:
    """Tell the Actions columns how much width the work area has; they fold below _NARROW_BELOW."""
    narrow = width < _NARROW_BELOW
    if narrow != _AUTO["narrow"]:
        _AUTO["narrow"] = narrow
        _AUTO["open"] = False
        refresh_inspectors()


def ink_shell(panel, title="Actions", width=280):
    """Open two-part layout for a tool: the work area and a quiet Actions
    column beside it (right by default; Settings can move it left or collapse
    it). Returns (root layout of the work area, layout of the Actions column)."""
    shell = QHBoxLayout(panel)
    shell.setContentsMargins(0, 0, 0, 0)
    shell.setSpacing(0)
    left = QWidget()
    root = QVBoxLayout(left)
    root.setContentsMargins(28, 22, 28, 14)
    root.setSpacing(14)
    insp = InspectorFrame(shell, left, title, width)
    panel.inspector = insp
    return root, insp.body_layout


class _GridAdder:
    """addWidget() that fills a small grid row by row (the Organizer's page
    tools, stacked in the Actions column)."""

    def __init__(self, cols=2):
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(8)
        self.cols = cols
        self.n = 0

    def addWidget(self, w):
        self.grid.addWidget(w, self.n // self.cols, self.n % self.cols)
        self.n += 1


class _WorkerPanelMixin:
    """The busy/cancel/shutdown contract MainWindow.closeEvent relies on.
    Panels set self._worker; everything else is shared."""

    _worker = None
    _busy = False
    _shutting_down = False

    def is_busy(self):
        return self._busy

    def stop_for_shutdown(self, wait_ms=15000):
        # Queued signals from the worker are still delivered after wait()
        # returns; the finished handlers must not raise a dialog over a
        # window that is closing.
        self._shutting_down = True
        if self._worker is None:
            return True
        self._worker.cancel()
        return self._worker.wait(wait_ms)

    def _notify(self, kind: str, title: str, text: str):
        """QMessageBox.<kind>, suppressed once the window is shutting down."""
        if self._shutting_down:
            return
        getattr(Msg, kind)(self, title, text)

    def _alert_done(self):
        """Flash the taskbar entry if the user has gone to another window
        while a batch ran. A no-op when the window is already active; no
        tray icon, no toast, nothing to dismiss."""
        try:
            win = self.window()
            if win is not None and not win.isActiveWindow():
                QApplication.alert(win, 0)
        except Exception:
            pass

    # Keyboard hooks MainWindow binds (Ctrl+O / Ctrl+Return / Ctrl+Shift+Z /
    # Esc): the NAME of a method, resolved at key-press time. Never store a
    # bound method on a QWidget instance - it makes a reference cycle, and a
    # QWidget torn down by the cyclic garbage collector segfaults PySide6
    # (reproduced 3/3 on RenamerPanel, 2026-09-02).
    SHORTCUT_OPEN = None
    SHORTCUT_RUN = None
    SHORTCUT_SAVE = None
    SHORTCUT_UNDO = None
    SHORTCUT_REFRESH = None

    def _cancel(self):
        if self._worker is not None:
            self._worker.cancel()


def _weak(callback):
    """A callable that reaches `callback` without keeping its owner alive.

    A bound method handed to a child widget (a drop zone, a settings panel)
    and stored there is a strong reference from child back to parent: a
    parent <-> child cycle the garbage collector has to tear down, which is
    the shape that segfaults PySide6 (preship §Custom PySide6). This keeps a
    WeakMethod instead and does nothing once the owner is gone."""
    if not isinstance(callback, types.MethodType):
        return callback               # None, a plain function, a builtin or a Qt slot
    ref = weakref.WeakMethod(callback)

    def call(*args, **kwargs):
        fn = ref()
        return fn(*args, **kwargs) if fn is not None else None
    return call


def _later(callback, *bound_args):
    """A zero-argument callable for QMenu.addAction / .connect that calls
    `callback(*bound_args)` through a weak reference. Replaces
    `lambda: self.method(x)`, which keeps `self` alive for as long as the
    menu or signal holds the lambda."""
    weak = _weak(callback)

    def call(*_ignored):
        return weak(*bound_args)
    return call


SEND_TARGETS = (("merger", "PDF Merger"), ("splitter", "PDF Splitter"),
                ("optimizer", "PDF Optimizer"), ("organizer", "Page Organizer"),
                ("renamer", "Bulk Renamer"))


def send_paths_from(panel, target: str, paths) -> bool:
    """Hand files to another tab, through the window that hosts both."""
    win = panel.window() if panel is not None else None
    fn = getattr(win, "send_paths", None)
    paths = [p for p in (paths or []) if isinstance(p, str) and p]
    if not callable(fn) or not paths:
        return False
    return bool(fn(target, paths))


def build_send_menu(parent, source: str, paths, title: str = "Send to"):
    """A menu listing the other tabs that can take these files. Built at click time,
    so `paths` is what the person sees right now."""
    menu = QMenu(title, parent)
    host = getattr(parent.window(), "send_paths", None) if parent is not None else None
    for key, label in SEND_TARGETS:
        if key == source:
            continue
        act = menu.addAction(label, _later(send_paths_from, parent, key, list(paths)))
        act.setEnabled(callable(host) and bool(paths))
    if source != "tray":
        menu.addSeparator()
        tray_fn = getattr(parent.window(), "add_to_tray", None) if parent is not None else None
        act = menu.addAction("File tray (keep them for later)", _later(add_to_tray_from, parent, list(paths)))
        act.setEnabled(callable(tray_fn) and bool(paths))
    return menu


def add_to_tray_from(panel, paths) -> bool:
    """Put files in the window's file tray (and show it) so any tool can pick them up later."""
    win = panel.window() if panel is not None else None
    fn = getattr(win, "add_to_tray", None)
    paths = [p for p in (paths or []) if isinstance(p, str) and p]
    if not callable(fn) or not paths:
        return False
    text = fn(paths)
    setter = getattr(panel, "_set_status", None)
    if callable(setter) and text:
        setter(text, "")
    return True


class DropZone(QLabel):
    """Drag-and-drop target. Emits via callback instead of Qt signal for simplicity."""

    def __init__(self, on_drop, text="DROP FILE HERE\n(original will be overwritten)"):
        super().__init__(text)
        self.setObjectName("dropZone")
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        # Scales with the window, but takes only DROPZONE_STRETCH parts of
        # the spare vertical space against SPACER_STRETCH parts for the
        # trailing spacer — i.e. 75% of what it used to claim when it was
        # added with a bare stretch=1 and absorbed everything.
        self.setMinimumHeight(DROPZONE_MIN_H)
        self.setContentsMargins(0, 34, 0, 0)    # room for the tray glyph above the text
        self.on_drop = _weak(on_drop)
        # A QLabel is not focusable by default, so a drop zone was invisible
        # to the keyboard and to a screen reader. Every tab that accepts work
        # only by drop was therefore mouse-only.
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("File drop area")
        self.setAccessibleDescription(
            "Drop files here, or press Enter to choose files.")
        self.on_activate = None

    def paintEvent(self, event):
        super().paintEvent(event)
        pm = glyph_pixmap("tray", theme_color("accent"), 34)
        if pm is not None:
            from PySide6.QtGui import QPainter
            painter = QPainter(self)
            br = self.fontMetrics().boundingRect(
                self.rect().adjusted(8, 34, -8, 0),
                int(Qt.TextWordWrap | Qt.AlignCenter), self.text())
            top = max(8, br.top() - 40)
            painter.drawPixmap((self.width() - 34) // 2, top, pm)
            painter.end()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            if callable(self.on_activate):
                self.on_activate()
                return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("dragActive", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event):
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event):
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        if paths:
            self.on_drop(paths)



# ---------------------------------------------------------------------
# Hover tips. One table, applied once when a panel is built, so a control
# added later without a tip is caught by tests/test_tooltips.py rather than
# discovered by a user hovering over nothing. Keyed by the control's visible
# text, accessible name or placeholder (lower-cased, arrows and dots dropped;
# a key ending in "*" matches a prefix, for buttons whose text carries a count).
# A tip written at the control's creation always wins over this table.
# ---------------------------------------------------------------------
def _tip_key(text: str) -> str:
    t = (text or "").replace("▾", "").replace("▸", "").replace("…", "")
    t = t.replace("...", "")
    return " ".join(t.lower().split()).strip(" :.")


_TIPS = {
    # shared
    "choose folder": "Pick the folder to work on (or drop a folder here)",
    "source folder": "Pick the folder the files are in (or drop one here)",
    "destination folder": "Pick the folder the files should go to (or drop one here)",
    "recent": "Folders and files you used lately",
    "cancel": "Stop what is running; anything already finished stays",
    "browse": "Choose from your computer",
    "clear": "Clear this",
    # merger
    "no file selected": "The PDF whose pages will be added onto each file you drop in",
    "choose file to merge": "Pick the file to merge onto (or drop it on the box above)",
    "open folder": "Open the folder holding the merged file",
    # splitter
    "first": "Split pages off the front of the PDF (the cover)",
    "last": "Split pages off the end of the PDF",
    "number of pages to split off the front": "How many pages to take from the front",
    "number of pages to split off the end": "How many pages to take from the end",
    "choose pdfs to split": "Pick the PDFs to split (or drop them on the box above)",
    "open split folder": "Open the folder the split files were written to",
    # optimizer
    "size limit in megabytes": "Aim for a file no bigger than this many MB",
    "size limit presets": "Pick a saved size limit, such as an upload limit",
    "choose pdfs": "Pick the PDFs to optimize (or drop them on the box above)",
    "open output folder": "Open the folder the finished files were written to",
    # organizer
    "pages of the selected pdf": "Drag pages to reorder, or right-click for rotate, move, remove and extract",
    "pdfs in the folder": "Click a PDF to open it; right-click for more choices",
    "find text in these pdfs": "Type the text to look for in every PDF in the folder (text layer only)",
    "search the text of every pdf in the folder": "Type the text to look for in every PDF in the folder (text layer only)",
    "search": "Search every PDF in the folder for this text (Enter)",
    "only pdfs with a match": "Hide PDFs where the search found nothing",
    # renamer
    "include subfolders": "Also rename files inside folders below this one",
    "pdf, docx (blank = all)": "Only touch these file types; leave blank for all",
    "use regular expressions": "Treat the Find box as a regular expression",
    "case sensitive": "Match upper and lower case exactly",
    "save as": "Save these settings as a preset",
    "delete": "Delete the selected item",
    "new": "Create a new profile",
    "duplicate": "Copy the selected profile to a new one",
    "rename": "Rename the selected profile",
    "import": "Load profiles from a file",
    "export": "Save profiles to a file",
    "keep the originals (copy instead of move)": "Leave the original files in place and write renamed copies",
    "lower-case the extension (.pdf -> .pdf)": "Turn .PDF into .pdf",
    "skip it and leave it alone": "Files with no matching keyword are left untouched",
    "leave the name alone, mark it already correct": "Files with no matching keyword keep their name and are marked done",
    "use a fallback ending instead": "Files with no matching keyword get the ending typed beside this",
    "add keyword": "Add a keyword row",
    "remove": "Remove the selected keyword row",
    "move up": "Move the selected keyword up (earlier rows win)",
    "move down": "Move the selected keyword down",
    "undo my changes": "Throw away edits made to this profile since you opened it",
    "save this profile": "Keep this profile's rules",
    "rename * files": "Rename the files listed in the preview",
    "rename 1 file": "Rename the file listed in the preview",
    "undo last rename": "Put the files back as they were",
    "open result folder": "Open the folder with the renamed files",
    # mover
    "move files now": "Move the matching files to the destination folder",
    "undo last move": "Put the last batch of files back where they were",
    "open destination": "Open the destination folder",
    # settings / help
    "vermilion": "Warm vermilion and charcoal colours (the default)", "light": "Light colours", "dark": "Dark colours", "periwinkle": "Soft purple-blue colours",
    "small": "Smaller text", "normal": "Standard text size", "large": "Larger text",
    "show pagey, the paper mascot, in confirmation and result boxes": "Show Pagey in message boxes; off gives plain Windows boxes",
    "open the optimized folder when a run finishes": "Open the output folder automatically after an optimize run",
    "resolution used when pages become pictures": "Detail used when a page has to be turned into a picture",
    "export presets": "Save your size-limit presets to a file",
    "import presets": "Load size-limit presets from a file",
    "regular expressions — recipes and cheat sheet": "Show or hide the regular-expression help",
    "advanced — folder names, suffixes, backups, undo depth, size-limit presets": "Show or hide the advanced settings",
    "splitter output folder name": "Name of the folder the Splitter writes into",
    "splitter cover-part suffix": "Ending added to the cover file name",
    "splitter remaining-part suffix": "Ending added to the remaining file name",
    "optimizer output folder name": "Name of the folder the Optimizer writes into",
    "page organizer output folder name": "Name of the folder the Page Organizer writes into",
    "page organizer thumbnail width (pixels)": "How big page pictures are in the Page Organizer",
    "page organizer page changes that can be undone": "How many page changes Undo can step back through on one PDF",
    "extract starts with delete after extracting ticked": "Tick the delete-after-extracting box by default each time Extract opens",
    "page organizer replaces the original file": "Overwrite the original after saving a copy in an Originals folder",
    "page organizer keeps the file's own scripts and programs": "Leave the file's own scripts and launch actions in the saved copy (pasted pages are always cleaned)",
    "merger backups kept (copies of overwritten files)": "How many old copies the Merger keeps",
    "undo depth (rename and move runs that can be undone)": "How many rename or move runs you can undo",
    "tab to open on launch": "Which tab the program starts on",
    "page organizer list refresh": "When the Page Organizer looks for PDFs added, removed or changed in its folder",
    "reader starting zoom": "How big pages are drawn when you open a PDF full size in the Page Organizer",
    "reader memory for drawn pages (mb)": "How much memory the full-size reader may use to keep drawn pages; lower it on a small computer",
    "refresh the pdf list": "Look for PDFs added, removed or changed in this folder (F5)",
    "mover: characters allowed after a file number": "Extra characters that may follow a file number and still match; blank = any",
    "optimizer size-limit presets, one per line, name = mb": "One preset per line, written as  name = MB",
    "save advanced settings": "Keep the advanced settings above",
    "reset to defaults": "Put every setting back to its original value",
    "open settings folder": "Open the folder where settings are stored",
    # extract dialog
    "folder to save the extracted pages in": "Where the new PDF will be written",
    "file name for the extracted pages": "Name for the new PDF (.pdf is added for you)",
    "extract": "Write the chosen pages to a new PDF",
    "delete the extracted pages from the pdf after extracting":
        "Also take these pages out of the PDF you are organizing once the new file is written (Undo brings them back)",
}


def _tip_for(widget):
    for raw in (getattr(widget, "text", None) and widget.text() if not isinstance(
            widget, (QTextEdit, QPlainTextEdit)) else "", widget.accessibleName(),
            getattr(widget, "placeholderText", lambda: "")()):
        k = _tip_key(raw if isinstance(raw, str) else "")
        if not k:
            continue
        if k in _TIPS:
            return _TIPS[k]
        for pat, tip in _TIPS.items():
            if pat.endswith("*") or "*" in pat:
                a, _, b = pat.partition("*")
                if k.startswith(a) and k.endswith(b):
                    return tip
    return ""


_TIP_TYPES = (QPushButton, QToolButton, QCheckBox, QRadioButton, QComboBox, QSpinBox,
              QLineEdit, QSlider, QAbstractItemView, QPlainTextEdit, QTextEdit)


# Controls with no text of their own (tables, boxes with only a placeholder,
# dropdowns), keyed by (owning class, attribute name).
_ATTR_TIPS = {
    ("OptimizerPanel", "table"): "One row per PDF: its size before and after, and what happened",
    ("RenamerPanel", "table"): "Preview of every rename; nothing changes until you press RENAME",
    ("RenamerPanel", "search_edit"): "Text to look for in each file name",
    ("RenamerPanel", "replace_edit"): "What to put in its place",
    ("RenamerPanel", "scope_combo"): "Which part of the name to change: the name, the extension or both",
    ("RenamerPanel", "transform_combo"): "Change the letter case of the name after replacing",
    ("RenamerPanel", "preset_combo"): "Load a saved set of rename settings",
    ("AutoRenamePanel", "profile_combo"): "Pick the profile whose rules decide the new names",
    ("AutoRenamePanel", "id_edit"): "Pattern that reads the reference number off the start of a file name",
    ("AutoRenamePanel", "tpl_edit"): "Shape of the new name; {id} is the number, {suffix} the keyword ending",
    ("AutoRenamePanel", "out_edit"): "Folder the renamed copies go into",
    ("AutoRenamePanel", "fallback_edit"): "Ending used when no keyword matches",
    ("AutoRenamePanel", "table"): "Keywords for this profile, checked top to bottom; the first match wins",
    ("AutoRenamePanel", "sample_edit"): "Type a sample file name to see what it would be renamed to",
    ("SettingsPanel", "shared_edit"): "Optional shared file so everyone uses the same profile rules",
    ("MoverPanel", "text_input"): "Paste or type the file numbers to move, one per line or separated by commas",
    ("OrganizerPanel", "grid"): "Drag pages to reorder, or right-click for rotate, move, remove and extract",
}

_TIPS.update({
    "find and replace": "Show or hide the find-and-replace rule",
    "auto rename - rename by profile": "Show or hide the rename-by-profile rules",
    "naming and folder settings": "Show or hide how names and the output folder are built",
})


def untipped(root):
    """Controls under `root` that still have no hover tip (read-only)."""
    out = []
    for w in root.findChildren(QWidget):
        if not isinstance(w, _TIP_TYPES) or isinstance(w, QHeaderView):
            continue
        par = w.parent()
        if type(w).__name__ == "QListView" or isinstance(par, (QComboBox, QSpinBox)):
            continue
        if w.objectName().startswith("qt_") or (par is not None and par.objectName().startswith("qt_")):
            continue
        if not w.toolTip().strip():
            out.append(w)
    return out


def apply_tooltips(root):
    """Give every control under `root` that has no tip one from _TIPS. Returns
    the controls it could not find a tip for (the test fails on any)."""
    missing = []
    for o in [root] + root.findChildren(QWidget):
        for attr, v in list(vars(o).items()):
            tip = _ATTR_TIPS.get((type(o).__name__, attr))
            if tip and isinstance(v, QWidget) and not v.toolTip().strip():
                v.setToolTip(tip)
    for w in root.findChildren(QWidget):
        if not isinstance(w, _TIP_TYPES) or isinstance(w, QHeaderView):
            continue
        par = w.parent()
        if type(w).__name__ == "QListView" or isinstance(par, (QComboBox, QSpinBox)):
            continue
        if w.objectName().startswith("qt_") or (par is not None and par.objectName().startswith("qt_")):
            continue
        if w.toolTip().strip():
            continue
        tip = _tip_for(w)
        if tip:
            w.setToolTip(tip)
        else:
            missing.append(w)
    return missing


def csv_safe(value) -> str:
    """A cell that a spreadsheet will show as text. A file called '=HYPERLINK(...)'
    or '@SUM(...)' would otherwise run as a formula when the results CSV is opened
    in Excel; a leading apostrophe makes it inert."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class FolderRow(QFrame):
    """A bordered row: [button] [Recent] [path label], sharp square edges.

    Also a drop target: dropping a folder from Explorer onto the row selects
    it, and a dropped FILE selects the folder it is in. `recent_key` names
    the ui_state list the Recent menu reads; omit it for no menu.
    """

    changed = Signal(str)

    def __init__(self, button_text, on_browse, recent_key: str = None):
        super().__init__()
        self.setObjectName("panel")
        self._recent_key = recent_key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)

        self.button = QPushButton(button_text)
        self.button.setObjectName("secondary")
        self.button.clicked.connect(on_browse)
        layout.addWidget(self.button)

        self.recent_btn = None
        if recent_key:
            self.recent_btn = QPushButton("Recent \u25be")
            self.recent_btn.setObjectName("secondary")
            self.recent_btn.setAccessibleName(f"{button_text}: recent folders")
            self.recent_btn.clicked.connect(self._show_recent)
            layout.addWidget(self.recent_btn)

        self.path_label = QLabel("No folder selected")
        self.path_label.setObjectName("pathLabel")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label, stretch=1)

        self.setAcceptDrops(True)
        self.setAccessibleDescription(
            "Drop a folder here, or use the button to browse.")

    def set_path(self, path: str, remember: bool = True):
        self.path_label.setText(path or "No folder selected")
        if path and remember and self._recent_key:
            push_recent(self._recent_key, path)
        self.changed.emit(path or "")

    def path(self) -> str:
        text = self.path_label.text()
        return "" if text == "No folder selected" else text

    def setEnabled(self, on: bool):
        # Only the controls: the path stays readable while a job runs.
        self.button.setEnabled(on)
        if self.recent_btn is not None:
            self.recent_btn.setEnabled(on)

    def _show_recent(self):
        items = recent_paths(self._recent_key) if self._recent_key else []
        menu = QMenu(self)
        if not items:
            act = menu.addAction("No recent folders")
            act.setEnabled(False)
        for item in items:
            menu.addAction(item).setData(item)
        menu.triggered.connect(self._recent_chosen)
        menu.exec(self.recent_btn.mapToGlobal(
            self.recent_btn.rect().bottomLeft()))
        menu.deleteLater()

    def _recent_chosen(self, action):
        path = action.data()
        if isinstance(path, str) and path:
            self.set_path(path)

    def _dropped_folder(self, event) -> str:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        for url in urls:
            local = url.toLocalFile()
            if not local:
                continue
            if os.path.isdir(local):
                return local
            if os.path.isfile(local):
                return os.path.dirname(local)
        return ""

    def dragEnterEvent(self, event):
        if self.button.isEnabled() and self._dropped_folder(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        folder = self._dropped_folder(event)
        if folder and self.button.isEnabled():
            self.set_path(folder)
            event.acceptProposedAction()
        else:
            event.ignore()


class MergerPanel(_WorkerPanelMixin, QWidget):
    SHORTCUT_OPEN = "choose_file"
    SHORTCUT_UNDO = "undo_merge"

    def __init__(self):
        super().__init__()

        self.reference_path = ""
        self._busy = False
        self._worker = None

        root, actions = ink_shell(self)

        header = QLabel("PDF Merger – Overwrite Mode")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(header)

        root.addSpacing(6)
        step1 = QLabel("1. SELECT REFERENCE PDF")
        step1.setObjectName("sectionTitle")
        root.addWidget(step1)

        ref_panel = QFrame()
        ref_panel.setObjectName("panel")
        ref_layout = QHBoxLayout(ref_panel)
        ref_layout.setContentsMargins(14, 10, 14, 10)

        self.ref_entry = QLineEdit()
        self.ref_entry.setPlaceholderText("No file selected")
        self.ref_entry.setReadOnly(True)
        ref_layout.addWidget(self.ref_entry, stretch=1)

        self.browse_btn = QPushButton("Browse")
        self.browse_btn.setObjectName("secondary")
        self.browse_btn.clicked.connect(self.load_reference)
        ref_layout.addWidget(self.browse_btn)
        self.recent_btn = QPushButton("Recent \u25be")
        self.recent_btn.setObjectName("secondary")
        self.recent_btn.setAccessibleName("Recent reference PDFs")
        self.recent_btn.clicked.connect(self._show_recent)
        ref_layout.addWidget(self.recent_btn)

        root.addWidget(ref_panel)

        root.addSpacing(10)
        step2 = QLabel("2. DRAG & DROP FILE TO MERGE")
        step2.setObjectName("sectionTitle")
        root.addWidget(step2)

        self.drop_zone = DropZone(self.handle_drop)
        self.drop_zone.on_activate = _weak(self.choose_file)
        root.addWidget(self.drop_zone, stretch=DROPZONE_STRETCH)

        # Drag-and-drop is the only way in without this, which makes the tab
        # unusable by keyboard, by a screen reader, and over Remote Desktop
        # where drops from Explorer frequently do not fire.
        btn_row = actions
        self.choose_btn = QPushButton("Choose File to Merge...")
        self.choose_btn.setObjectName("primaryAction")
        self.choose_btn.clicked.connect(self.choose_file)
        btn_row.addWidget(self.choose_btn)
        self.open_btn = QPushButton("Open Folder")
        self.open_btn.setObjectName("secondary")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_last)
        btn_row.addWidget(self.open_btn)
        self.undo_btn = QPushButton("Undo Last Merge")
        self.undo_btn.setObjectName("secondary")
        self.undo_btn.setToolTip("Put back the file the last merge overwrote, as it was before the merge "
                                 "(only while that file is unchanged since)")
        self.undo_btn.setEnabled(merge_undo_info() is not None)
        self.undo_btn.clicked.connect(self.undo_merge)
        btn_row.addWidget(self.undo_btn)
        self.send_btn = QPushButton("Send To \u25be")
        self.send_btn.setObjectName("secondary")
        self.send_btn.setToolTip("Open the merged file in another tab")
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._show_send)
        btn_row.addWidget(self.send_btn)
        actions.addStretch(1)

        root.addStretch(SPACER_STRETCH)

        self.status = QLabel("Ready")
        self.status.setObjectName("status")
        root.addWidget(self.status)

        self._undoing = False
        self._last_base = ""
        # No isfile() here: a stat on an offline share blocks the first
        # paint. merge_onto reports a missing reference in plain language.
        last_ref = load_ui_state().get("merger_last_reference", "")
        if isinstance(last_ref, str) and last_ref:
            self._set_reference(last_ref, remember=False)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the PDF to merge onto",
            os.path.dirname(self._last_base) if self._last_base else "",
            "PDF Files (*.pdf)")
        if path:
            self.handle_drop([path])

    def load_reference(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Reference PDF",
            os.path.dirname(self.reference_path) if self.reference_path else "",
            "PDF Files (*.pdf)")
        if path:
            self._set_reference(path)

    def _set_reference(self, path: str, remember: bool = True):
        self.reference_path = path
        self.ref_entry.setText(path)
        self._set_status("Reference loaded.", "ok")
        if remember:
            push_recent("merger_references", path)
            update_ui_state(merger_last_reference=path)

    def _show_recent(self):
        items = recent_paths("merger_references")
        menu = QMenu(self)
        if not items:
            menu.addAction("No recent reference PDFs").setEnabled(False)
        for item in items:
            menu.addAction(item).setData(item)
        menu.triggered.connect(self._reference_chosen)
        menu.exec(self.recent_btn.mapToGlobal(self.recent_btn.rect().bottomLeft()))
        menu.deleteLater()

    def _reference_chosen(self, action):
        path = action.data()
        if isinstance(path, str) and path:
            self._set_reference(path)

    def _open_last(self):
        if not open_in_explorer(self._last_base):
            self._set_status("That folder is no longer there.", "error")

    def handle_drop(self, paths):
        if self._busy:
            return

        base_path = paths[0]

        if not self.reference_path:
            Msg.warning(self, "Action Required", "Please browse and select your reference PDF first.")
            return

        info = classify_drop([base_path])
        if not info["pdfs"]:
            Msg.critical(self, "Invalid File", "Please drop a PDF file.\n\n" + describe_drop(info))
            return

        if os.path.normcase(os.path.abspath(base_path)) == os.path.normcase(os.path.abspath(self.reference_path)):
            self._set_status("That is the reference PDF itself. Drop a different file to merge it onto.", "error")
            Msg.warning(self, "Same File",
                        f"'{os.path.basename(base_path)}' is the reference PDF you chose. Merging a file "
                        "onto itself would double it, so nothing was changed.\n\n"
                        "Drop a different file, or pick another reference.")
            return

        if len(paths) > 1:
            Msg.information(
                self, "Single File Only",
                f"The merger handles one file at a time. Using:\n{os.path.basename(base_path)} "
                f"({human_size(info['bytes'])})\n\nNot merged: "
                + ", ".join(os.path.basename(p) for p in paths[1:6])
                + (f" and {len(paths) - 6} more" if len(paths) > 6 else "")
            )

        confirm = Msg.question(
            self, "Confirm Overwrite",
            f"This will overwrite:\n{os.path.basename(base_path)} ({human_size(info['bytes'])})\n\n"
            f"by appending pages from:\n{os.path.basename(self.reference_path)}\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        self.execute_merge(base_path, self.reference_path)

    def execute_merge(self, base_path: str, ref_path: str):
        """Off the UI thread. One item, so there is no Cancel: merge_onto
        writes atomically and keeps a backup, so closing mid-merge is safe
        for the original either way."""
        self._set_busy(True)
        self._set_status("Merging and overwriting...", "busy")
        self._worker = _BatchWorker(
            [base_path], lambda p: merge_onto(p, ref_path), self)
        self._worker.item_done.connect(self._on_merged)
        self._worker.item_failed.connect(self._on_merge_failed)
        self._worker.finished_all.connect(self._on_finished)
        self._worker.start()

    def _show_send(self):
        menu = self._build_send_menu()
        menu.exec(self.send_btn.mapToGlobal(self.send_btn.rect().bottomLeft()))
        menu.deleteLater()

    def _build_send_menu(self):
        return build_send_menu(self, "merger", [self._last_base] if self._last_base else [])

    def receive_paths(self, paths):
        """Another tab sent a file: it becomes the file to merge onto."""
        pdfs = classify_drop(paths)["pdfs"][:1]
        if not pdfs:
            self._set_status("That is not a PDF the merger can use.", "error")
            return False
        self.handle_drop(pdfs)
        return True

    def receive_pair(self, base: str, ref: str) -> bool:
        """From the tray: `ref` is the reference PDF (its pages are added) and `base` is the file
        that gets them. The usual overwrite confirmation still follows."""
        if self._busy:
            return False
        if not (classify_drop([base])["pdfs"] and classify_drop([ref])["pdfs"]):
            self._set_status("Both files must be PDFs the merger can use.", "error")
            return False
        if os.path.normcase(os.path.abspath(base)) == os.path.normcase(os.path.abspath(ref)):
            self._set_status("Pick two different PDFs: a file cannot be merged into itself.", "error")
            return False
        self._set_reference(ref)
        self.handle_drop([base])
        return True

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.drop_zone.setAcceptDrops(not busy)
        self.choose_btn.setEnabled(not busy)
        self.browse_btn.setEnabled(not busy)
        self.recent_btn.setEnabled(not busy)
        self.open_btn.setEnabled((not busy) and bool(self._last_base))
        self.send_btn.setEnabled((not busy) and bool(self._last_base) and os.path.isfile(self._last_base))
        self.undo_btn.setEnabled((not busy) and merge_undo_info() is not None)

    def undo_merge(self):
        """Take the last merge back: the overwritten file returns as it was."""
        if self._busy:
            return
        rec = merge_undo_info()
        if rec is None:
            self._set_status("There is no merge to undo.", "error")
            return
        name = os.path.basename(rec["original"])
        if Msg.question(self, "Undo Last Merge",
                        f"Put {name} back the way it was before the last merge?\n\n"
                        "The pages that merge added will be removed again.",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._undoing = True
        self._set_busy(True)
        self._set_status("Restoring...", "busy")
        self._worker = _BatchWorker([rec["original"]], lambda _p: undo_last_merge(), self)
        self._worker.item_done.connect(self._on_merged)
        self._worker.item_failed.connect(self._on_merge_failed)
        self._worker.finished_all.connect(self._on_finished)
        self._worker.start()

    def _on_merged(self, _i, base_path, _result):
        self._last_base = base_path
        self.open_btn.setEnabled(True)
        if self._undoing:
            self._set_status(f"Restored {os.path.basename(base_path)} to before the merge.", "ok")
            self._notify("information", "Undo Complete",
                         f"{os.path.basename(base_path)} is back the way it was before the merge.")
            return
        self._set_status("Done! File overwritten. Undo Last Merge puts it back.", "ok")
        self._notify("information", "Success",
                     f"Original file successfully updated:\n{os.path.basename(base_path)}")

    def _on_merge_failed(self, _i, _base_path, message):
        self._set_status("Could not restore." if self._undoing else "Error occurred.", "error")
        self._notify("critical", "Error", message)

    def _on_finished(self, _cancelled):
        self._undoing = False
        self._set_busy(False)
        self._worker = None
        self._alert_done()

    def _set_status(self, text: str, kind: str = ""):
        """kind: "" | "ok" | "error" | "busy" — resolved by the stylesheet,
        so status colours follow the active theme instead of being frozen
        into an inline stylesheet at call time."""
        self.status.setText(text)
        self.status.setProperty("kind", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


class SplitterPanel(_WorkerPanelMixin, QWidget):
    SHORTCUT_OPEN = "choose_files"

    def __init__(self):
        super().__init__()

        self._busy = False
        self._worker = None
        self._done = []
        self._failed = []
        self._total = 0
        self._non_pdfs = 0
        self._skipped_note = ""

        root, actions = ink_shell(self)

        header = QLabel("PDF Splitter")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(header)

        root.addSpacing(6)
        step1 = QLabel("1. CHOOSE WHAT TO SPLIT OFF")
        step1.setObjectName("sectionTitle")
        root.addWidget(step1)

        opts_panel = QFrame()
        opts_panel.setObjectName("panel")
        grid = QGridLayout(opts_panel)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(2, 1)

        def page_combo(name):
            combo = QComboBox()
            combo.setAccessibleName(name)
            combo.setFixedWidth(124)
            combo.setFocusPolicy(Qt.StrongFocus)   # wheel alone must not grab focus or change it
            for _n in (1, 2, 3):
                combo.addItem(f"{_n} page" if _n == 1 else f"{_n} pages", _n)
            return combo

        self.radio_first = QRadioButton("First")
        self.combo_first = page_combo("Number of pages to split off the front")
        self.first_label = QLabel("")
        self.first_label.setObjectName("hint")
        self.radio_last = QRadioButton("Last")
        self.combo_last = page_combo("Number of pages to split off the end")
        self.last_label = QLabel("")
        self.last_label.setObjectName("hint")
        for r, (rb, cb, lb) in enumerate(((self.radio_first, self.combo_first, self.first_label),
                                          (self.radio_last, self.combo_last, self.last_label))):
            grid.addWidget(rb, r, 0)
            grid.addWidget(cb, r, 1)
            grid.addWidget(lb, r, 2)
        self.radio_first.setChecked(True)

        # 1 = split from the front, 2 = from the back. Each dropdown picks its
        # own row, so choosing a number never needs a second click on the radio.
        self.split_group = QButtonGroup(self)
        self.split_group.addButton(self.radio_first, 1)
        self.split_group.addButton(self.radio_last, 2)
        self.combo_first.activated.connect(self._first_picked)
        self.combo_last.activated.connect(self._last_picked)
        # Merely clicking (or tabbing into) a dropdown selects its row too -
        # the user should not have to pick a value just to choose the row.
        self._combo_rows = {self.combo_first: self.radio_first,
                            self.combo_last: self.radio_last}
        for _cb in self._combo_rows:
            _cb.installEventFilter(self)
        self.combo_first.currentIndexChanged.connect(self._refresh_labels)
        self.combo_last.currentIndexChanged.connect(self._refresh_labels)
        root.addWidget(opts_panel)

        state = load_ui_state()
        saved = state.get("splitter_split_after")
        if saved in (1, 2, 3) and not isinstance(saved, bool):
            if state.get("splitter_from_end") is True:
                self.combo_last.setCurrentIndex(saved - 1)
                self.radio_last.setChecked(True)
            else:
                self.combo_first.setCurrentIndex(saved - 1)
        self._refresh_labels()
        self.split_group.idToggled.connect(self._remember_split)
        self.combo_first.currentIndexChanged.connect(self._remember_split)
        self.combo_last.currentIndexChanged.connect(self._remember_split)

        root.addSpacing(10)
        step2 = QLabel("2. DRAG & DROP PDF FILES")
        step2.setObjectName("sectionTitle")
        root.addWidget(step2)

        self.drop_zone = DropZone(
            self.handle_drop,
            text=(
                "DROP PDF FILES HERE\n"
                f"(originals are kept; output goes to a '{setting('split_dir_name')}' folder\n"
                "created next to the source files)"
            ),
        )
        self.drop_zone.on_activate = _weak(self.choose_files)
        root.addWidget(self.drop_zone, stretch=DROPZONE_STRETCH)

        btn_row = actions
        self.choose_btn = QPushButton("Choose PDFs to Split...")
        self.choose_btn.setObjectName("primaryAction")
        self.choose_btn.clicked.connect(self.choose_files)
        btn_row.addWidget(self.choose_btn)
        self.open_btn = QPushButton("Open Split Folder")
        self.open_btn.setObjectName("secondary")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_last)
        btn_row.addWidget(self.open_btn)
        self.send_btn = QPushButton("Send To \u25be")
        self.send_btn.setObjectName("secondary")
        self.send_btn.setToolTip("Open the files this split just made in another tab")
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._show_send)
        btn_row.addWidget(self.send_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("secondary")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self.cancel_btn)
        actions.addStretch(1)

        root.addStretch(SPACER_STRETCH)

        self.status = QLabel("Ready")
        self.status.setObjectName("status")
        root.addWidget(self.status)

        self._out_files = []
        self._out_dirs = []
        self._last_dir = load_ui_state().get("splitter_last_dir", "")

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose PDFs to split",
            self._last_dir if os.path.isdir(self._last_dir) else "",
            "PDF Files (*.pdf)")
        if paths:
            self.handle_drop(paths)

    def eventFilter(self, obj, event):
        rb = getattr(self, "_combo_rows", {}).get(obj)
        if rb is not None and obj.isEnabled():
            # Only a deliberate click selects the row. FocusIn used to be here
            # too, but Qt moves focus by itself (tabbing past the checked
            # radio, or when a run disables the widgets), and that flipped the
            # choice to "Last" behind the user's back.
            if event.type() == QEvent.MouseButtonPress:
                rb.setChecked(True)
            elif event.type() == QEvent.Wheel and not obj.hasFocus():
                return True      # scrolling the page must not change a value
        return super().eventFilter(obj, event)

    def split_from_end(self) -> bool:
        return self.split_group.checkedId() == 2

    def split_point(self) -> int:
        combo = self.combo_last if self.split_from_end() else self.combo_first
        return int(combo.currentData() or 1)

    def _first_picked(self, _i=0):
        self.radio_first.setChecked(True)

    def _last_picked(self, _i=0):
        self.radio_last.setChecked(True)

    def _refresh_labels(self, *_args):
        f = int(self.combo_first.currentData() or 1)
        n = int(self.combo_last.currentData() or 1)
        self.first_label.setText(
            f"the first {'page' if f == 1 else plural(f, 'page')} {'becomes' if f == 1 else 'become'} the "
            "cover; the rest stays together")
        self.last_label.setText(
            f"the last {'page' if n == 1 else plural(n, 'page')} {'is' if n == 1 else 'are'} split off; "
            "everything before stays together")

    def _remember_split(self, *_args):
        update_ui_state(splitter_split_after=self.split_point(),
                        splitter_from_end=self.split_from_end())

    def _open_last(self):
        opened = [d for d in self._out_dirs if open_in_explorer(d)]
        if not opened:
            self._set_status("That folder is no longer there.", "error")

    def handle_drop(self, paths):
        if self._busy:
            return

        info = classify_drop(paths)
        pdfs = info["pdfs"]
        non_pdfs = len(info["skipped"])
        self._skipped_note = describe_drop(info)

        if not pdfs:
            self._set_status("Nothing was split: none of those are PDFs.", "error")
            Msg.critical(self, "Invalid File",
                         "Please drop one or more PDF files.\n\n" + self._skipped_note)
            return

        n = self.split_point()
        from_end = self.split_from_end()
        self._last_dir = os.path.dirname(pdfs[0])
        update_ui_state(splitter_last_dir=self._last_dir)
        if from_end:
            what = f"Split off the last {plural(n, 'page')} of {plural(len(pdfs))}?"
            outputs = (f"<name>{LAST_SUFFIX_FMT.format(n=n)}.pdf and "
                       f"<name>{setting('remaining_suffix')}.pdf")
        else:
            what = f"Split {plural(len(pdfs))} after page {n}?"
            outputs = (f"<name>{setting('cover_suffix')}.pdf and "
                       f"<name>{setting('remaining_suffix')}.pdf")
        confirm = Msg.question(
            self, "Confirm Split",
            f"{what}\n{self._skipped_note}\n\n"
            f"Originals are left untouched.\n"
            f"Output: {outputs}\n"
            f"in a '{setting('split_dir_name')}' folder beside each source file.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        self.execute_split(pdfs, n, non_pdfs, from_end)

    def execute_split(self, pdfs, n: int, non_pdfs: int, from_end: bool = False):
        """Off the UI thread, one file per item; Cancel stops between files."""
        self._done, self._failed = [], []
        self._out_dirs = []
        self._out_files = []
        self._total, self._non_pdfs = len(pdfs), non_pdfs
        self._set_busy(True)
        self._set_status(f"Splitting {plural(len(pdfs))}...", "busy")
        self._worker = _BatchWorker(
            pdfs, lambda p: split_pdf(p, n, from_end=from_end), self)
        self._worker.item_done.connect(self._on_item_done)
        self._worker.item_failed.connect(self._on_item_failed)
        self._worker.finished_all.connect(self._on_finished)
        self._worker.start()

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.drop_zone.setAcceptDrops(not busy)
        self.choose_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        for w in (self.radio_first, self.radio_last, self.combo_first, self.combo_last):
            w.setEnabled(not busy)
        self.open_btn.setEnabled((not busy) and bool(self._out_dirs))
        self.send_btn.setEnabled((not busy) and bool(getattr(self, "_out_files", [])))

    def _show_send(self):
        menu = self._build_send_menu()
        menu.exec(self.send_btn.mapToGlobal(self.send_btn.rect().bottomLeft()))
        menu.deleteLater()

    def _build_send_menu(self):
        return build_send_menu(self, "splitter", [p for p in getattr(self, "_out_files", []) if os.path.isfile(p)])

    def receive_paths(self, paths):
        """Another tab sent files: they go through the same confirm as a drop."""
        if not classify_drop(paths)["pdfs"]:
            self._set_status("None of those are PDFs the splitter can use.", "error")
            return False
        self.handle_drop(list(paths))
        return True

    def _progress(self):
        self._set_status(
            f"Splitting... {len(self._done) + len(self._failed)} of {self._total}",
            "busy")

    def _on_item_done(self, _i, path, result):
        self._done.append(os.path.basename(path))
        try:
            self._out_files.extend(p for p in result if isinstance(p, str))
        except TypeError:
            pass
        try:
            out_dir = os.path.dirname(result[0])
        except Exception:
            out_dir = os.path.join(os.path.dirname(path), setting("split_dir_name"))
        if out_dir not in self._out_dirs:
            self._out_dirs.append(out_dir)
        self._progress()

    def _on_item_failed(self, _i, _path, message):
        self._failed.append(message)
        self._progress()

    def _on_finished(self, cancelled: bool):
        self._set_busy(False)
        self._worker = None
        self.open_btn.setEnabled(bool(self._out_dirs))
        self._alert_done()
        done, failed = self._done, self._failed
        summary = [f"Split: {len(done)}"]
        if cancelled:
            summary.append(
                f"Cancelled: {self._total - len(done) - len(failed)} not started")
        if self._non_pdfs:
            summary.append(f"Skipped: {self._non_pdfs} ({getattr(self, '_skipped_note', '').split('Skipped: ')[-1].rstrip('.')})")
        if failed:
            summary.append(f"Failed: {len(failed)}")
        msg = "\n".join(summary)

        if failed:
            self._set_status("Completed with errors.", "error")
            self._notify("warning", "Completed With Errors",
                         msg + "\n\n" + "\n".join(failed))
        elif cancelled:
            self._set_status(f"Cancelled after {plural(len(done))}.", "")
            self._notify("information", "Cancelled", msg)
        else:
            self._set_status("Done!", "ok")
            self._notify("information", "Success", msg)

    def _set_status(self, text: str, kind: str = ""):
        """kind: "" | "ok" | "error" | "busy" — resolved by the stylesheet,
        so status colours follow the active theme instead of being frozen
        into an inline stylesheet at call time."""
        self.status.setText(text)
        self.status.setProperty("kind", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


class AutoRenamePanel(QWidget):
    """Auto Rename — create and edit profiles, and pick the active one.

    Everything about auto-renaming lives here: the profile list, the naming
    shape, the ordered keyword table and a live "try a filename" box. Staff
    never have to go looking on another tab for the rules they are using.

    Emits `changed` whenever anything that affects the preview moves, so the
    Renamer re-plans. Owns no preview and no action button - the Renamer tab's
    existing preview table and RENAME button serve both modes.
    """

    changed = Signal()

    COL_KEYWORD, COL_SUFFIX, COL_MATCH, COL_CASE = range(4)
    REFRESH_MS = 120

    def __init__(self):
        super().__init__()
        self.profiles, load_notes = load_profiles()
        self._loading = False
        self._dirty = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh)
        self._build()
        self._reload_profiles()
        if load_notes:
            self._say(load_notes[0], "error")

    # ---- build -----------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        pick = QHBoxLayout()
        pick.addWidget(QLabel("Profile:"))
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        pick.addWidget(self.profile_combo, stretch=1)
        root.addLayout(pick)

        # Three across, two rows. In one row these ran off the edge of the
        # 420px control pane and the last two were unreachable.
        btns = QGridLayout()
        for i, (text, slot) in enumerate((("New...", self._on_new),
                                          ("Duplicate", self._on_duplicate),
                                          ("Rename...", self._on_rename_profile),
                                          ("Delete", self._on_delete),
                                          ("Import...", self._on_import),
                                          ("Export...", self._on_export))):
            b = QPushButton(text)
            b.setObjectName("secondary")
            b.clicked.connect(slot)
            btns.addWidget(b, i // 3, i % 3)
        root.addLayout(btns)

        self.desc = QLabel("")
        self.desc.setObjectName("hint")
        self.desc.setWordWrap(True)
        root.addWidget(self.desc)

        self.locked_note = QLabel(
            "This profile comes from the shared profile file, so it is the same "
            "on every machine and cannot be edited here. Press Duplicate to "
            "make your own editable copy.")
        self.locked_note.setObjectName("status")
        self.locked_note.setWordWrap(True)
        self.locked_note.setVisible(False)
        root.addWidget(self.locked_note)

        # Advisory, never blocking: legal settings that are probably a mistake.
        # Said out loud BEFORE they cost somebody an afternoon, rather than
        # documented somewhere nobody reads.
        self.warn_label = QLabel("")
        self.warn_label.setObjectName("status")
        self.warn_label.setProperty("kind", "warn")
        self.warn_label.setWordWrap(True)
        self.warn_label.setVisible(False)
        root.addWidget(self.warn_label)

        opts_body = QWidget()
        opts = QVBoxLayout(opts_body)
        opts.setContentsMargins(0, 0, 0, 0)
        shape = QGridLayout()
        shape.addWidget(QLabel("Find the ID:"), 0, 0)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText(r"^(\d+)   - digits at the start")
        self.id_edit.textChanged.connect(self._touch)
        shape.addWidget(self.id_edit, 0, 1)
        self.id_status = QLabel("")
        self.id_status.setObjectName("status")
        self.id_status.setWordWrap(True)
        shape.addWidget(self.id_status, 1, 1)

        shape.addWidget(QLabel("New name:"), 2, 0)
        self.tpl_edit = QLineEdit()
        self.tpl_edit.setPlaceholderText("{id}{suffix}{ext}")
        self.tpl_edit.textChanged.connect(self._touch)
        shape.addWidget(self.tpl_edit, 2, 1)
        tpl_hint = QLabel("Fields: " + "  ".join("{%s}" % f for f in TEMPLATE_FIELDS))
        tpl_hint.setObjectName("hint")
        tpl_hint.setWordWrap(True)
        shape.addWidget(tpl_hint, 3, 1)

        shape.addWidget(QLabel("Put them in:"), 4, 0)
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("Renamed")
        self.out_edit.textChanged.connect(self._touch)
        shape.addWidget(self.out_edit, 4, 1)

        self.keep_box = QCheckBox("Keep the originals (copy instead of move)")
        self.keep_box.toggled.connect(self._touch)
        shape.addWidget(self.keep_box, 5, 1)

        self.lower_box = QCheckBox("Lower-case the extension (.PDF -> .pdf)")
        self.lower_box.toggled.connect(self._touch)
        shape.addWidget(self.lower_box, 6, 1)
        opts.addLayout(shape)

        nm = QLabel("When no keyword matches a file:")
        opts.addWidget(nm)
        # Radios, not a combo: three fixed options, all visible, nothing to
        # open and nothing to scroll.
        self.nm_group = QButtonGroup(self)
        for i, (key, text) in enumerate((
                ("skip", "Skip it and leave it alone"),
                ("keep", "Leave the name alone, mark it already correct"),
                ("fallback", "Use a fallback ending instead:"))):
            rb = QRadioButton(text)
            rb.setProperty("nm", key)
            self.nm_group.addButton(rb, i)
            opts.addWidget(rb)
        self.nm_group.idToggled.connect(self._touch)
        self.fallback_edit = QLineEdit()
        self.fallback_edit.setPlaceholderText("-Misc")
        self.fallback_edit.textChanged.connect(self._touch)
        opts.addWidget(self.fallback_edit)

        self.options_spoiler = Collapsible("Naming and folder settings",
                                           opts_body, start_open=False)
        root.addWidget(self.options_spoiler)

        kw_title = QLabel("Keywords, in order - the first one that matches wins")
        kw_title.setObjectName("sectionTitle")
        root.addWidget(kw_title)
        order_hint = QLabel("Each file is checked top to bottom and stops at the "
                            "first match, so put the most specific keywords first.")
        order_hint.setObjectName("hint")
        order_hint.setWordWrap(True)
        root.addWidget(order_hint)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Look for", "Ending", "Match", "Aa"])
        self.table.horizontalHeaderItem(self.COL_CASE).setToolTip("Match upper/lower case exactly")
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(self.COL_KEYWORD, QHeaderView.Stretch)
        head.setSectionResizeMode(self.COL_SUFFIX, QHeaderView.Stretch)
        # Fixed, not ResizeToContents: the dropdown reports a wide hint and was
        # squeezing the two columns that matter down to an ellipsis.
        head.setSectionResizeMode(self.COL_MATCH, QHeaderView.Fixed)
        head.setSectionResizeMode(self.COL_CASE, QHeaderView.Fixed)
        self.table.setColumnWidth(self.COL_MATCH, 118)
        self.table.setColumnWidth(self.COL_CASE, 34)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(220)
        self.table.itemChanged.connect(self._touch)
        self.table.currentCellChanged.connect(self._touch)
        root.addWidget(self.table)

        self.match_help = QLabel("")
        self.match_help.setObjectName("hint")
        self.match_help.setWordWrap(True)
        root.addWidget(self.match_help)

        rowbtns = QHBoxLayout()
        for text, slot in (("Add keyword", self._on_add_rule),
                           ("Remove", self._on_remove_rule),
                           ("Move up", lambda: self._move(-1)),
                           ("Move down", lambda: self._move(1))):
            b = QPushButton(text)
            b.setObjectName("secondary")
            b.clicked.connect(slot)
            rowbtns.addWidget(b)
        root.addLayout(rowbtns)

        try_row = QGridLayout()
        try_row.addWidget(QLabel("Try a filename:"), 0, 0)
        self.sample_edit = QLineEdit()
        self.sample_edit.setPlaceholderText("1234719592 County Sheriff Return.PDF")
        self.sample_edit.textChanged.connect(self._touch)
        try_row.addWidget(self.sample_edit, 0, 1)
        self.sample_out = QLabel("")
        self.sample_out.setObjectName("status")
        self.sample_out.setWordWrap(True)
        try_row.addWidget(self.sample_out, 1, 1)
        root.addLayout(try_row)

        save_row = QHBoxLayout()
        self.say = QLabel("")
        self.say.setObjectName("status")
        self.say.setWordWrap(True)
        save_row.addWidget(self.say, stretch=1)
        self.revert_btn = QPushButton("Undo my changes")
        self.revert_btn.setObjectName("secondary")
        self.revert_btn.clicked.connect(self._load_current)
        save_row.addWidget(self.revert_btn)
        self.save_btn = QPushButton("Save this profile")
        self.save_btn.setObjectName("secondary")
        self.save_btn.clicked.connect(self._on_save)
        save_row.addWidget(self.save_btn)
        root.addLayout(save_row)

    # ---- helpers ---------------------------------------------------------
    def _is_locked(self) -> bool:
        p = self._get(self.profile_combo.currentText())
        return bool(p is not None and p.locked)

    def _say(self, text, kind=""):
        self.say.setText(text)
        self.say.setProperty("kind", kind)
        self.say.style().unpolish(self.say)
        self.say.style().polish(self.say)

    def _touch(self, *_args):
        """Swallows whatever the signal carried. NEVER connect a signal
        straight to a slot with an int overload."""
        if self._loading:
            return
        self._dirty = True
        # _refresh rebuilds the profile from every widget, validates two
        # regexes and re-enables ~10 controls; per keystroke that is felt.
        self._refresh_timer.start(self.REFRESH_MS)
        self.changed.emit()

    def names(self):
        return [p.name for p in self.profiles]

    def _get(self, name):
        for p in self.profiles:
            if p.name == name:
                return p
        return None

    def _reload_profiles(self, select=None):
        self._loading = True
        self.profile_combo.clear()
        self.profile_combo.addItems(self.names())
        # Fall back to the profile this user had last time. Somebody who does
        # one profile all day should not re-pick it every morning.
        if not select:
            select = load_ui_state().get("auto_rename_last_profile")
        if select and select in self.names():
            self.profile_combo.setCurrentText(select)
        self._loading = False
        self._load_current()

    def _remember_profile(self):
        name = self.profile_combo.currentText()
        if not name:
            return
        state = load_ui_state()
        if state.get("auto_rename_last_profile") != name:
            state["auto_rename_last_profile"] = name
            save_ui_state(state)

    def _on_profile_changed(self, *_a):
        if not self._loading:
            self._remember_profile()
            self._load_current()

    def _load_current(self):
        p = self._get(self.profile_combo.currentText())
        self._loading = True
        try:
            if p is None:
                self.table.setRowCount(0)
                return
            self.desc.setText(p.description)
            self.id_edit.setText(p.id_pattern)
            self.tpl_edit.setText(p.template)
            self.out_edit.setText(p.output_folder)
            self.keep_box.setChecked(p.keep_originals)
            self.lower_box.setChecked(p.lowercase_ext)
            self.fallback_edit.setText(p.fallback_suffix)
            self.nm_group.button(NO_MATCH_ACTIONS.index(p.on_no_match)).setChecked(True)
            self._fill_table(p.rules)
        finally:
            self._loading = False
        self._dirty = False
        self._refresh()
        self.changed.emit()

    def _fill_table(self, rules):
        self.table.setRowCount(len(rules))
        for r, rule in enumerate(rules):
            self.table.setItem(r, self.COL_KEYWORD, QTableWidgetItem(rule.keyword))
            self.table.setItem(r, self.COL_SUFFIX, QTableWidgetItem(rule.suffix))
            self._set_match_combo(r, rule.match_mode)
            c = QTableWidgetItem("")
            # BOTH the flag AND an explicit check state, or it never renders.
            c.setFlags((c.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            c.setCheckState(Qt.Checked if rule.case_sensitive else Qt.Unchecked)
            self.table.setItem(r, self.COL_CASE, c)

    def _set_match_combo(self, row, mode):
        """A dropdown, not a typed cell: 'contains' is one of seven choices and
        nobody should have to remember the exact wording of the other six."""
        combo = QComboBox()
        combo.addItems([MATCH_MODE_SHORT[m] for m in MATCH_MODES])
        combo.setCurrentText(MATCH_MODE_SHORT.get(mode, MATCH_MODE_SHORT["contains"]))
        combo.currentIndexChanged.connect(self._touch)
        combo.setToolTip("How the file name must match this keyword: contains, starts with, and so on")
        self.table.setCellWidget(row, self.COL_MATCH, combo)

    def _match_mode(self, row):
        w = self.table.cellWidget(row, self.COL_MATCH)
        if w is None:
            return "contains"
        for k, v in MATCH_MODE_SHORT.items():
            if v == w.currentText():
                return k
        return "contains"

    def _cell_text(self, row, col):
        it = self.table.item(row, col)
        return it.text().strip() if it else ""

    def current_profile(self):
        """The live, unsaved state of the form."""
        rules = []
        for r in range(self.table.rowCount()):
            kw = self._cell_text(r, self.COL_KEYWORD)
            if not kw:
                continue
            case_item = self.table.item(r, self.COL_CASE)
            rules.append(KeywordRule(
                keyword=kw,
                suffix=self._cell_text(r, self.COL_SUFFIX),
                match_mode=self._match_mode(r),
                case_sensitive=bool(case_item and case_item.checkState() == Qt.Checked),
            ))
        btn = self.nm_group.checkedButton()
        return RenameProfile(
            name=self.profile_combo.currentText(),
            id_pattern=self.id_edit.text() or r"^(\d+)",
            template=self.tpl_edit.text() or "{id}{suffix}{ext}",
            rules=rules,
            on_no_match=(btn.property("nm") if btn else "skip"),
            fallback_suffix=self.fallback_edit.text(),
            output_folder=self.out_edit.text().strip(),
            keep_originals=self.keep_box.isChecked(),
            lowercase_ext=self.lower_box.isChecked(),
            description=self.desc.text(),
        )

    def _refresh(self):
        p = self.current_profile()

        gate = screen_regex(p.id_pattern)
        if gate:
            self._set_kind(self.id_status, gate, "error")
        elif re.compile(p.id_pattern).groups < 1:
            self._set_kind(self.id_status,
                           "Put brackets around the part that is the ID, "
                           "like ^(\\d+)", "error")
        else:
            self._set_kind(self.id_status, "Looks good.", "ok")

        locked = self._is_locked()
        for w in (self.id_edit, self.tpl_edit, self.out_edit, self.fallback_edit,
                  self.keep_box, self.lower_box, self.table):
            w.setEnabled(not locked)
        for b in self.nm_group.buttons():
            b.setEnabled(not locked)
        self.save_btn.setVisible(not locked)
        self.revert_btn.setVisible(not locked)
        self.locked_note.setVisible(locked)

        self.keep_box.setEnabled(bool(p.output_folder) and not locked)
        if not p.output_folder and self.keep_box.isChecked():
            self._loading = True
            self.keep_box.setChecked(False)   # nothing to keep them separate from
            self._loading = False

        r = self.table.currentRow()
        self.match_help.setText(
            MATCH_MODE_HELP.get(self._match_mode(r), "")
            if 0 <= r < self.table.rowCount()
            else "Pick a keyword row to see what its match type does.")

        errs = p.validate()
        self.save_btn.setEnabled(not errs)
        self.revert_btn.setEnabled(self._dirty)
        warns = p.warnings()
        self.warn_label.setText(warns[0] if warns else "")
        self.warn_label.setVisible(bool(warns))
        if errs:
            self._say(errs[0], "error")
        elif locked:
            self._say("Shared profile - changed in the shared file, not from here.", "")
        else:
            self._say("Unsaved changes." if self._dirty else "Saved.",
                      "" if self._dirty else "ok")

        sample = self.sample_edit.text().strip()
        if not sample:
            self._set_kind(self.sample_out,
                           "Type a filename above to see what it becomes.", "")
        else:
            new, status, note = p.new_name(sample)
            if status == ST_OK:
                self._set_kind(self.sample_out, f"{sample}   \u2192   {new}", "ok")
            else:
                self._set_kind(self.sample_out, note or status,
                               "error" if status == ST_INVALID else "")

    @staticmethod
    def _set_kind(label, text, kind):
        label.setText(text)
        label.setProperty("kind", kind)
        label.style().unpolish(label)
        label.style().polish(label)

    # ---- row actions -----------------------------------------------------
    def _on_add_rule(self):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self._loading = True
        self.table.setItem(r, self.COL_KEYWORD, QTableWidgetItem(""))
        self.table.setItem(r, self.COL_SUFFIX, QTableWidgetItem(""))
        self._set_match_combo(r, "contains")
        c = QTableWidgetItem("")
        c.setFlags((c.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
        c.setCheckState(Qt.Unchecked)
        self.table.setItem(r, self.COL_CASE, c)
        self._loading = False
        self.table.setCurrentCell(r, self.COL_KEYWORD)
        self._touch()

    def _on_remove_rule(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._touch()

    def _move(self, delta):
        """Reorder by rebuilding from the rule list.

        Swapping QTableWidgetItems leaves the cell WIDGETS behind, so the
        match-type dropdowns would stay put while the text moved and the rows
        would silently change meaning.
        """
        r = self.table.currentRow()
        t = r + delta
        if r < 0 or t < 0 or t >= self.table.rowCount():
            return
        rules = self.current_profile().rules
        if r >= len(rules) or t >= len(rules):
            return
        rules[r], rules[t] = rules[t], rules[r]
        self._loading = True
        self._fill_table(rules)
        self._loading = False
        self.table.setCurrentCell(t, self.COL_KEYWORD)
        self._touch()

    # ---- profile actions --------------------------------------------------
    def _persist(self, select=None):
        try:
            save_profiles(self.profiles)
        except OSError as e:
            Msg.warning(self, "Could not save",
                                f"Your profiles could not be saved:\n\n{e}")
        keep = list(self.profiles)
        self.profiles, notes = load_profiles()
        if not self.profiles:
            self.profiles = keep
        self._reload_profiles(select)
        if notes:
            self._say(notes[0], "error")

    def _on_new(self):
        name, ok = QInputDialog.getText(self, "New profile", "Profile name:")
        if ok and name.strip():
            p = blank_profile(unique_profile_name(name.strip(), self.names()))
            self.profiles.append(p)
            self._persist(p.name)

    def _on_duplicate(self):
        cur = self._get(self.profile_combo.currentText())
        if cur is None:
            return
        p = cur.copy_as(unique_profile_name(cur.name + " copy", self.names()))
        self.profiles.append(p)
        self._persist(p.name)

    def _on_rename_profile(self):
        old = self.profile_combo.currentText()
        if self._is_locked():
            Msg.information(
                self, "Shared profile",
                f"'{old}' comes from the shared profile file and is named there. "
                f"Press Duplicate to make your own copy under a different name.")
            return
        name, ok = QInputDialog.getText(self, "Rename profile", "Profile name:", text=old)
        if not (ok and name.strip()) or name.strip() == old:
            return
        p = self.current_profile()
        p.name = unique_profile_name(name.strip(), [n for n in self.names() if n != old])
        for i, existing in enumerate(self.profiles):
            if existing.name == old:
                self.profiles[i] = p
                break
        self._persist(p.name)

    def _on_delete(self):
        name = self.profile_combo.currentText()
        if not name:
            return
        if self._is_locked():
            Msg.information(
                self, "Shared profile",
                f"'{name}' comes from the shared profile file. Remove it there "
                f"if it should go - deleting it here would only put it back the "
                f"next time the app starts.")
            return
        if Msg.question(
                self, "Delete profile",
                f"Delete '{name}' and all of its keywords?\n\nThis cannot be undone.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.profiles = [p for p in self.profiles if p.name != name]
        if not self.profiles:
            self.profiles = seed_profiles()
        self._persist()

    def _on_save(self):
        if self._is_locked():
            return
        p = self.current_profile()
        errs = p.validate()
        if errs:
            Msg.warning(self, "Cannot save",
                                "This profile cannot be saved yet:\n\n"
                                + "\n".join("- " + e for e in errs))
            return
        for i, existing in enumerate(self.profiles):
            if existing.name == p.name:
                self.profiles[i] = p
                break
        else:
            self.profiles.append(p)
        self._dirty = False
        self._persist(p.name)

    def _on_export(self):
        name = self.profile_combo.currentText()
        p = self._get(name)
        if p is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export profile",
                                              f"{name}.json",
                                              "Profile (*.json)")
        if not path:
            return
        try:
            export_profile(p, path)
            self._say(f"Exported to {os.path.basename(path)}.", "ok")
        except OSError as e:
            Msg.warning(self, "Export failed", str(e))

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import profile", "",
                                              "Profile (*.json)")
        if not path:
            return
        try:
            incoming = import_profiles(path)
        except Exception:
            Msg.warning(
                self, "Import failed",
                "That file is not a profile this tool can read.\n\n"
                "It should be a .json file exported from here.")
            return
        for p in incoming:
            p.name = unique_profile_name(p.name, self.names())
            self.profiles.append(p)
        self._persist(incoming[-1].name if incoming else None)
        self._say(f"Imported {plural(len(incoming), 'profile')}.", "ok")


class RenamerPanel(_WorkerPanelMixin, QWidget):
    """Configurable bulk rename with a live preview and one-level undo.

    Files only. Folders are never collected and never renamed.
    """
    SHORTCUT_OPEN = "browse_folder"
    SHORTCUT_RUN = "execute_rename"
    SHORTCUT_UNDO = "execute_undo"

    DEBOUNCE_MS = 60
    RESCAN_MS = 250

    def _queue_refresh(self, *_args):
        """Restart the debounce, discarding whatever the signal carried."""
        self._debounce.start(self.DEBOUNCE_MS)

    def _queue_rescan(self, *_args):
        self._rescan_timer.start(self.RESCAN_MS)

    def __init__(self):
        super().__init__()

        self._busy = False
        self._worker = None
        self._files = []
        self._excluded = set()
        self._plan = None
        self._presets = load_presets()

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self.refresh_preview)

        # Scope changes (subfolders tick, extension filter) re-walk the folder,
        # which on a network share is the expensive half - one walk per burst
        # of typing, not one per keystroke.
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(self.RESCAN_MS)
        self._rescan_timer.timeout.connect(self.rescan)

        self._splitter_save = QTimer(self)
        self._splitter_save.setSingleShot(True)
        self._splitter_save.timeout.connect(self._flush_splitter)

        root, actions = ink_shell(self)
        root.setSpacing(8)

        header = QLabel("Bulk Renamer")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(header)

        # =============================================================
        # Split pane: controls left, preview right.
        # The control stack is a fixed height no matter how tall the
        # window gets, so in the old top-down layout it ate 90% of the
        # tab at the minimum size and the preview showed ONE row. Giving
        # the table its own full-height column is the whole point.
        # =============================================================
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)

        # ---------------- LEFT: controls -----------------------------
        left = QWidget()
        lc = QVBoxLayout(left)
        lc.setContentsMargins(0, 0, 0, 0)
        lc.setSpacing(8)

        step1 = QLabel("1. SELECT FOLDER")
        step1.setObjectName("sectionTitle")
        lc.addWidget(step1)

        self.folder_row = FolderRow("Choose Folder", self.browse_folder,
                                    recent_key="renamer_folders")
        self.folder_row.changed.connect(self._folder_row_changed)
        lc.addWidget(self.folder_row)

        scope_panel = QFrame()
        scope_panel.setObjectName("panel")
        scope_layout = QVBoxLayout(scope_panel)
        scope_layout.setContentsMargins(14, 10, 14, 10)
        scope_layout.setSpacing(4)

        self.recurse_box = QCheckBox("Include subfolders")
        self.recurse_box.stateChanged.connect(self._queue_rescan)
        scope_layout.addWidget(self.recurse_box)

        ext_row = QHBoxLayout()
        ext_row.addWidget(QLabel("Only extensions:"))
        self.ext_filter = QLineEdit()
        self.ext_filter.setPlaceholderText("pdf, docx   (blank = all)")
        self.ext_filter.textChanged.connect(self._queue_rescan)
        ext_row.addWidget(self.ext_filter, stretch=1)
        scope_layout.addLayout(ext_row)

        lc.addWidget(scope_panel)

        step2 = QLabel("2. RENAME RULE")
        step2.setObjectName("sectionTitle")
        lc.addWidget(step2)

        rule_panel = QFrame()
        rule_panel.setObjectName("panel")
        grid = QGridLayout(rule_panel)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QLabel("Search for:"), 0, 0)
        self.search_edit = QLineEdit()
        grid.addWidget(self.search_edit, 0, 1)

        grid.addWidget(QLabel("Replace with:"), 1, 0)
        self.replace_edit = QLineEdit()
        grid.addWidget(self.replace_edit, 1, 1)

        self.regex_box = QCheckBox("Use regular expressions")
        self.case_box = QCheckBox("Case sensitive")
        grid.addWidget(self.regex_box, 2, 1)
        grid.addWidget(self.case_box, 3, 1)

        self.mode_hint = QLabel()
        self.mode_hint.setObjectName("status")
        self.mode_hint.setWordWrap(True)
        grid.addWidget(self.mode_hint, 4, 1)
        self.regex_box.toggled.connect(self._update_mode_hint)

        grid.addWidget(QLabel("Apply to:"), 5, 0)
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Filename only", "name")
        self.scope_combo.addItem("Extension only", "ext")
        self.scope_combo.addItem("Filename + extension", "both")
        grid.addWidget(self.scope_combo, 5, 1)

        grid.addWidget(QLabel("Case:"), 6, 0)
        self.transform_combo = QComboBox()
        self.transform_combo.addItem("Leave as is", "none")
        self.transform_combo.addItem("UPPERCASE", "upper")
        self.transform_combo.addItem("lowercase", "lower")
        self.transform_combo.addItem("Title Case", "title")
        grid.addWidget(self.transform_combo, 6, 1)

        # Both rename modes are spoilers, and exactly one is open at a time.
        # With two rule sets on one tab and a single shared preview, an
        # ambiguous "which one is running?" is the failure that renames the
        # wrong files - so the open spoiler owns the preview, and opening one
        # closes the other.
        self._manual_body = QWidget()
        _mb = QVBoxLayout(self._manual_body)
        _mb.setContentsMargins(0, 0, 0, 0)
        _mb.setSpacing(8)
        _mb.addWidget(rule_panel)

        # NOTE: never connect a signal directly to self._debounce.start.
        # QTimer.start has a start(msec) overload, so any signal carrying an
        # int (stateChanged, currentIndexChanged, valueChanged) silently
        # overwrites the debounce interval with its own payload — a spinbox
        # value of 5000 would become a 5-second delay. Swallow the arguments.
        for w in (self.search_edit, self.replace_edit):
            w.textChanged.connect(self._queue_refresh)
        for w in (self.regex_box, self.case_box):
            w.stateChanged.connect(self._queue_refresh)
        for w in (self.scope_combo, self.transform_combo):
            w.currentIndexChanged.connect(self._queue_refresh)

        preset_panel = QFrame()
        preset_panel.setObjectName("panel")
        pv = QVBoxLayout(preset_panel)
        pv.setContentsMargins(14, 10, 14, 10)
        pv.setSpacing(5)
        pv.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        pv.addWidget(self.preset_combo)
        pbtns = QHBoxLayout()
        save_btn = QPushButton("Save As")
        save_btn.setObjectName("secondary")
        save_btn.clicked.connect(self.save_preset)
        pbtns.addWidget(save_btn)
        del_btn = QPushButton("Delete")
        del_btn.setObjectName("secondary")
        del_btn.clicked.connect(self.delete_preset)
        pbtns.addWidget(del_btn)
        pv.addLayout(pbtns)
        _mb.addWidget(preset_panel)

        self.manual_spoiler = Collapsible("Find and replace", self._manual_body,
                                          start_open=True)
        self.manual_spoiler.toggle.clicked.connect(self._on_mode_toggled)
        lc.addWidget(self.manual_spoiler)

        # ---------------- Auto Rename ---------------------------------
        self.setAcceptDrops(True)
        self.auto_panel = AutoRenamePanel()
        self.auto_panel.changed.connect(self._queue_refresh)
        self.auto_spoiler = Collapsible("Auto Rename - rename by profile",
                                        self.auto_panel, start_open=False)
        self.auto_spoiler.toggle.clicked.connect(self._on_mode_toggled)
        lc.addWidget(self.auto_spoiler)

        self.mode_note = QLabel("Find and replace is on. Open Auto Rename below "
                                "to rename by profile instead.")
        self.mode_note.setObjectName("hint")
        self.mode_note.setWordWrap(True)
        lc.addWidget(self.mode_note)
        self._reload_preset_combo()
        self.preset_combo.currentIndexChanged.connect(self.apply_preset)

        lc.addStretch(1)

        # The control stack is taller than a 1366x768 laptop can show at the
        # window minimum. Rather than raise the floor above what those screens
        # have, the column scrolls — nothing is ever clipped, and at normal
        # window sizes the scrollbar never appears.
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(400)
        splitter.addWidget(left_scroll)

        # ---------------- RIGHT: preview -----------------------------
        right = QWidget()
        rc = QVBoxLayout(right)
        rc.setContentsMargins(0, 0, 0, 0)
        rc.setSpacing(8)

        step3 = QLabel("3. PREVIEW — UNTICK ANY FILE TO LEAVE IT ALONE")
        step3.setObjectName("sectionTitle")
        rc.addWidget(step3)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Original", "New name", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        head.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)
        rc.addWidget(self.table, stretch=1)

        peek_panel = QFrame()
        peek_panel.setObjectName("panel")
        pkl = QVBoxLayout(peek_panel)
        pkl.setContentsMargins(14, 10, 14, 10)
        pkl.setSpacing(2)
        pk_title = QLabel("PREVIEW OF THE SELECTED FILE")
        pk_title.setObjectName("sectionTitle")
        pkl.addWidget(pk_title)
        self.peek_label = QLabel("Click a row to see what is inside that file.")
        self.peek_label.setObjectName("peek")
        self.peek_label.setWordWrap(True)
        self.peek_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.peek_label.setMinimumHeight(72)
        self.peek_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        pkl.addWidget(self.peek_label)
        rc.addWidget(peek_panel)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        # setSizes before the splitter has been laid out is discarded — the
        # first layout recomputes an even split. Apply it in showEvent instead.
        splitter.splitterMoved.connect(self._save_splitter)
        self.splitter = splitter
        self._splitter_applied = False
        root.addSpacing(12)                # same title-to-first-section gap as the other tabs
        root.addWidget(splitter, stretch=1)

        self.status = QLabel("Choose a folder to begin.")
        self.status.setObjectName("status")
        root.addWidget(self.status)

        self.rename_btn = QPushButton("Rename 0 Files")
        self.rename_btn.setObjectName("primaryAction")
        self.rename_btn.setEnabled(False)
        self.rename_btn.clicked.connect(self.execute_rename)
        actions.addWidget(self.rename_btn)

        self.undo_btn = QPushButton("Undo Last Rename")
        self.undo_btn.setObjectName("secondary")
        self.undo_btn.clicked.connect(self.execute_undo)
        actions.addWidget(self.undo_btn)

        # Appears only after a run that produced an output folder. Everybody
        # wants to look at the result, every time.
        self.open_btn = QPushButton("OPEN RESULT FOLDER")
        self.open_btn.setObjectName("secondary")
        self.open_btn.clicked.connect(self._open_result)
        self.open_btn.setVisible(False)
        actions.addWidget(self.open_btn)
        actions.addStretch(1)
        self._result_folder = ""

        self._sync_undo_button()
        self._update_mode_hint(self.regex_box.isChecked())

        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)

        last = load_ui_state().get("renamer_last_folder", "")
        if isinstance(last, str) and last:
            # Restore where the operator left off. set_path emits `changed`,
            # which rescans on a daemon thread - the tab paints at once even
            # if the folder is on a share that is down this morning.
            self.folder_row.set_path(last, remember=False)

    def _table_menu(self, pos):
        menu = self._build_table_menu(pos)
        if menu is not None:
            menu.exec(self.table.viewport().mapToGlobal(pos))

    def _build_table_menu(self, pos):
        """The menu without exec(), so it can be inspected headlessly."""
        item = self.table.itemAt(pos)
        row = item.row() if item is not None else -1
        first = self.table.item(row, 0) if row >= 0 else None
        path = first.data(Qt.UserRole) if first is not None else None
        menu = QMenu(self)
        if path:
            menu.addAction("Open file", lambda: open_file(path))
            menu.addAction("Open containing folder", lambda: open_in_explorer(path))
            menu.addAction("Copy path", lambda: QApplication.clipboard().setText(path))
            menu.addSeparator()
            if path in self._excluded:
                menu.addAction("Include this file", lambda: first.setCheckState(Qt.Checked))
            else:
                menu.addAction("Exclude this file", lambda: first.setCheckState(Qt.Unchecked))
        sel_rows = sorted({i.row() for i in self.table.selectedItems()})
        if len(sel_rows) > 1:
            menu.addAction(f"Exclude {len(sel_rows)} selected",
                           _later(self._set_rows_checked, sel_rows, False))
            menu.addAction(f"Include {len(sel_rows)} selected",
                           _later(self._set_rows_checked, sel_rows, True))
        return None if menu.isEmpty() else menu

    def _set_rows_checked(self, rows, checked: bool):
        self.table.blockSignals(True)
        try:
            for r in rows:
                it = self.table.item(r, 0)
                p = it.data(Qt.UserRole) if it is not None else None
                if not p:
                    continue
                it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                (self._excluded.discard if checked else self._excluded.add)(p)
        finally:
            self.table.blockSignals(False)
        self.refresh_preview()

    def showEvent(self, event):
        super().showEvent(event)
        if self._splitter_applied:
            return
        self._splitter_applied = True
        saved = load_ui_state().get(RENAMER_SPLIT_KEY)
        if (isinstance(saved, list) and len(saved) == 2
                and all(isinstance(v, (int, float)) and v > 0 for v in saved)):
            self.splitter.setSizes([int(v) for v in saved])
        else:
            total = max(self.splitter.width(), CONTROL_PANE_W + 400)
            left = max(CONTROL_PANE_W, int(total * RENAMER_SPLIT_LEFT))
            self.splitter.setSizes([left, total - left])

    SPLITTER_SAVE_MS = 400

    def _save_splitter(self, *_args):
        """splitterMoved fires per mouse-move sample; write once per drag."""
        self._splitter_save.start(self.SPLITTER_SAVE_MS)

    def _flush_splitter(self):
        update_ui_state(**{RENAMER_SPLIT_KEY: self.splitter.sizes()})

    def hideEvent(self, event):
        # A drag that ends right before the tab is switched still lands.
        if self._splitter_save.isActive():
            self._splitter_save.stop()
            self._flush_splitter()
        super().hideEvent(event)

    def _update_mode_hint(self, on: bool):
        if on:
            self.search_edit.setPlaceholderText(r"pattern, e.g.  ^ABC_(\d+)")
            self.replace_edit.setPlaceholderText(
                r"\1 inserts a captured group"
            )
            self.mode_hint.setText(
                "Regex ON: . ^ $ * + ? ( ) [ ] are special. Use \\1 \\2 for groups."
            )
        else:
            self.search_edit.setPlaceholderText("text to find — plain text, a dot means a dot")
            self.replace_edit.setPlaceholderText(
                "replacement"
            )
            self.mode_hint.setText("Plain text: everything is matched literally.")

    # ---- rule plumbing -------------------------------------------------

    def current_rule(self) -> RenameRule:
        return RenameRule(
            search=self.search_edit.text(),
            replace=self.replace_edit.text(),
            use_regex=self.regex_box.isChecked(),
            case_sensitive=self.case_box.isChecked(),
            match_scope=self.scope_combo.currentData(),
            transform=self.transform_combo.currentData(),
        )

    def _apply_rule_to_widgets(self, rule: RenameRule):
        blockers = [self.search_edit, self.replace_edit, self.regex_box,
                    self.case_box, self.scope_combo, self.transform_combo]
        for w in blockers:
            w.blockSignals(True)
        self.search_edit.setText(rule.search)
        self.replace_edit.setText(rule.replace)
        self.regex_box.setChecked(rule.use_regex)
        self.case_box.setChecked(rule.case_sensitive)
        self.scope_combo.setCurrentIndex(max(0, self.scope_combo.findData(rule.match_scope)))
        self.transform_combo.setCurrentIndex(max(0, self.transform_combo.findData(rule.transform)))
        for w in blockers:
            w.blockSignals(False)
        self.refresh_preview()

    # ---- presets -------------------------------------------------------

    def _reload_preset_combo(self):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("(none)", None)
        for name in sorted(self._presets):
            self.preset_combo.addItem(name, name)
        self.preset_combo.blockSignals(False)

    def apply_preset(self):
        name = self.preset_combo.currentData()
        if name and name in self._presets:
            self._apply_rule_to_widgets(self._presets[name])

    def save_preset(self):
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in self._presets:
            confirm = Msg.question(
                self, "Overwrite Preset", f"A preset named '{name}' already exists. Replace it?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if confirm != QMessageBox.Yes:
                return
        # Re-read first. This dict is cached at construction, so a preset
        # imported on the Settings tab afterwards was silently erased by the
        # next save here - verified: the save path never called load_presets().
        try:
            self._presets = load_presets()
        except Exception:
            pass
        self._presets[name] = self.current_rule()
        try:
            save_presets(self._presets)
        except OSError as e:
            Msg.critical(self, "Error", f"Could not save preset: {e}")
            return
        self._reload_preset_combo()
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(name)))

    def delete_preset(self):
        name = self.preset_combo.currentData()
        if not name:
            return
        confirm = Msg.question(
            self, "Delete Preset", f"Delete preset '{name}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return
        self._presets.pop(name, None)
        try:
            save_presets(self._presets)
        except OSError as e:
            Msg.critical(self, "Error", f"Could not save presets: {e}")
            return
        self._reload_preset_combo()

    # ---- scanning and preview -------------------------------------------

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder", self.folder_row.path() or "")
        if folder:
            self.folder_row.set_path(folder)

    def receive_folder(self, folder: str) -> bool:
        """From the tray: work on this folder."""
        if not os.path.isdir(folder):
            self._set_status("That folder is not there any more.", "error")
            return False
        self.folder_row.set_path(folder)
        return True

    def receive_paths(self, paths):
        """Another tab sent files: the Renamer works on a folder, so it opens the folder they are in."""
        dirs = {os.path.dirname(os.path.abspath(p)) for p in paths if isinstance(p, str) and p}
        if len(dirs) != 1:
            self._set_status("Those files are in different folders. Send files from one folder.", "error")
            return False
        self.folder_row.set_path(dirs.pop())
        return True

    def _folder_row_changed(self, folder: str):
        """One path in for every way a folder can be chosen: Browse, Recent,
        a drop on the row, a drop on the panel, or the restore at startup."""
        if folder:
            update_ui_state(renamer_last_folder=folder)
        self.rescan()

    def rescan(self):
        """Walk the folder OFF the UI thread. os.walk on a network share is
        the slowest thing this tab does, and on an offline share it hangs;
        a daemon thread keeps the window alive either way. Results from a
        scan that a newer scan has superseded are dropped."""
        folder = self.folder_row.path()
        self._scan_seq = getattr(self, "_scan_seq", 0) + 1
        seq = self._scan_seq
        if not folder:
            self._files = []
            self._excluded.clear()
            self.refresh_preview()
            return
        recurse, exts = self.recurse_box.isChecked(), self.ext_filter.text()
        self._set_status("Reading folder...", "busy")

        def scan():
            if not os.path.isdir(folder):
                raise FileNotFoundError(folder)
            return (seq, collect_files(folder, recurse, exts),
                    find_orphan_temps(folder, recurse))

        _IoProbe(self, scan, self._scan_done).start()
        self._scan_folder = folder

    def _scan_done(self, result):
        problem = ""
        if isinstance(result, Exception):
            if isinstance(result, FileNotFoundError) and getattr(self, "_scan_folder", ""):
                forget_recent("renamer_folders", self._scan_folder)
                problem = "That folder is not reachable right now."
            else:
                problem = f"Could not read the folder ({result})."
            self._files = []
        else:
            seq, files, orphans = result
            if seq != self._scan_seq:
                return                      # a newer scan is on its way
            self._files = files
        self._excluded.clear()
        self.refresh_preview()              # sets its own status line...
        if problem:
            self._set_status(problem, "error")   # ...which the problem outranks
        elif not isinstance(result, Exception) and orphans:
            first = os.path.basename(orphans[0])
            self._set_status(
                f"{plural(len(orphans), 'leftover temporary file')} from an "
                f"interrupted rename in this folder (e.g. {first}). The original "
                f"names are in the undo log: press Undo, or rename them by hand.",
                "error")

    def _on_item_changed(self, item):
        if item.column() != 0:
            return
        path = item.data(Qt.UserRole)
        if not path:
            return
        if item.checkState() == Qt.Checked:
            self._excluded.discard(path)
        else:
            self._excluded.add(path)
        # A tick is a discrete action, not typing — refresh immediately.
        self.refresh_preview()

    def _open_result(self):
        folder = self._result_folder or getattr(self, "_folder", "")
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # -- drag and drop -----------------------------------------------------
    # The Merger and Splitter both take drops; this tab made you click Choose
    # Folder. Same app, same gesture, different answer.
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        folders = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if os.path.isfile(p)]
        if folders:
            self.folder_row.set_path(folders[0])
            self._folder = folders[0]
            self.rescan()
        elif files:
            # Dropping FILES means "these files", not "this folder" - so the
            # folder is taken from them and everything else in it is excluded
            # rather than silently swept in.
            self._folder = os.path.dirname(files[0])
            self.folder_row.set_path(self._folder)
            self.rescan()
            keep = {os.path.abspath(f) for f in files}
            self._excluded = {p for p in self._files
                              if os.path.abspath(p) not in keep}
            self.refresh_preview()

    def _on_mode_toggled(self, *_a):
        """Keep exactly one mode open. The sender is the one just clicked."""
        sender = self.sender()
        if sender is self.auto_spoiler.toggle and self.auto_spoiler.toggle.isChecked():
            self.manual_spoiler.toggle.setChecked(False)
            self.manual_spoiler._sync()
        elif sender is self.manual_spoiler.toggle and self.manual_spoiler.toggle.isChecked():
            self.auto_spoiler.toggle.setChecked(False)
            self.auto_spoiler._sync()
        self._queue_refresh()

    def auto_mode(self) -> bool:
        return bool(getattr(self, "auto_spoiler", None)
                    and self.auto_spoiler.toggle.isChecked())

    def refresh_preview(self):
        included = [p for p in self._files if p not in self._excluded]
        auto = self.auto_mode()
        rule_error = ""

        if auto:
            profile = self.auto_panel.current_profile()
            plan = build_profile_plan(included, profile)
            if profile.copies:
                self.mode_note.setText(
                    f"Auto Rename is on. Your originals are left exactly as they "
                    f"are - a renamed COPY of each matching file goes into a "
                    f"'{profile.output_folder}' folder.")
            elif profile.output_folder:
                self.mode_note.setText(
                    f"Auto Rename is on. Matching files are MOVED into a "
                    f"'{profile.output_folder}' folder under their new names.")
            else:
                self.mode_note.setText(
                    "Auto Rename is on. Files are renamed where they are.")
        else:
            self.mode_note.setText(
                "Find and replace is on. Open Auto Rename below to rename by "
                "profile instead.")
            rule = self.current_rule()
            try:
                plan = build_plan(included, rule)
            except RenameRuleError as e:
                plan = RenamePlan([])
                rule_error = str(e)

        self._plan = plan

        rows = list(plan.rows) + [
            RenameRow(src=p, dst="", status=ST_EXCLUDED)
            for p in sorted(self._excluded)
        ]

        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)

        # Stretch resize modes recalculate on every setItem, which is most of
        # the fill cost. Pin the columns during the fill, restore afterwards.
        head = self.table.horizontalHeader()
        for col in range(3):
            head.setSectionResizeMode(col, QHeaderView.Fixed)

        if self.table.rowCount() != len(rows):
            self.table.setRowCount(len(rows))

        for i, row in enumerate(rows):
            name_item = self.table.item(i, 0)
            if name_item is None:
                # A checkable item needs BOTH the flag and an explicit check
                # state, or the checkbox silently never renders.
                name_item = QTableWidgetItem()
                name_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                self.table.setItem(i, 0, name_item)
                self.table.setItem(i, 1, QTableWidgetItem())
                self.table.setItem(i, 2, QTableWidgetItem())

            name_item.setText(row.src_name)
            name_item.setCheckState(
                Qt.Unchecked if row.status == ST_EXCLUDED else Qt.Checked
            )
            name_item.setData(Qt.UserRole, row.src)

            self.table.item(i, 1).setText(row.dst_name)

            # Colour carries the scan: files the rule will actually change are
            # full-strength, files it leaves alone are muted, problems are red.
            # A 400-file list is unreadable if every row looks equally important.
            if row.status in BLOCKING_STATUSES:
                fg = QColor(theme_color("red"))
                name_fg = QColor(theme_color("red"))
            elif row.status == ST_OK:
                fg = QColor(theme_color("green"))
                name_fg = QColor(theme_color("text"))
            else:                       # unchanged / excluded — rule misses it
                fg = QColor(theme_color("muted"))
                name_fg = QColor(theme_color("muted"))

            name_item.setForeground(name_fg)
            self.table.item(i, 1).setForeground(name_fg)

            status_item = self.table.item(i, 2)
            label = status_text(row.status)
            status_item.setText(label if not row.note else f"{label} — {row.note}")
            status_item.setForeground(fg)

        head.setSectionResizeMode(0, QHeaderView.Stretch)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        head.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)

        if rule_error:
            self._set_status(rule_error, "error")
            self.rename_btn.setEnabled(False)
            self.rename_btn.setText("Rename 0 Files")
            return

        if not self._files:
            self._set_status("No files found in that folder.", "")
        elif plan.blocking_count:
            self._set_status(
                f"{plural(plan.blocking_count, 'problem')} must be resolved or excluded "
                f"before renaming.", "error"
            )
        elif plan.ok_count:
            self._set_status(
                f"{plural(plan.ok_count)} ready to "
                f"{'copy and rename' if plan.copies else 'rename'}.", "ok")
        elif auto:
            self._set_status("No file matches this profile's keywords.", "")
        elif (self.scope_combo.currentData() == "name" and not self.regex_box.isChecked()
              and re.search(r"\.[A-Za-z0-9]{1,5}$", self.search_edit.text().strip())):
            self._set_status("No files would change. 'Apply to' is Filename only, which leaves the "
                             "ending (.pdf) out; choose 'Filename + extension' to search it too.", "")
        else:
            self._set_status("No files would change with this rule.", "")

        verb = "Copy and Rename" if plan.copies else "Rename"
        self.rename_btn.setText(f"{verb} {plan.ok_count} {'File' if plan.ok_count == 1 else 'Files'}")
        self.rename_btn.setEnabled(plan.is_applicable and not self._busy)

    # ---- actions ---------------------------------------------------------

    def execute_rename(self):
        if self._busy or not self._plan or not self._plan.is_applicable:
            return

        count = self._plan.ok_count
        copies = getattr(self._plan, "copies", False)
        what = (f"Make a renamed copy of {plural(count)}?\n\n"
                f"Your original files will not be touched."
                if copies else f"Rename {plural(count)}?")
        confirm = Msg.question(
            self, "Confirm Rename",
            what + "\n\nYou can reverse this with 'Undo Last Rename' - "
                   "including after closing and reopening the app. Only the "
                   "most recent run can be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        self._busy = True
        self.rename_btn.setEnabled(False)
        self.undo_btn.setEnabled(False)
        self._set_status(f"{'Copying' if copies else 'Renaming'} "
                         f"{plural(count)}...", "busy")

        label = (f"Auto Rename: {self.auto_panel.profile_combo.currentText()}"
                 if self.auto_mode() else "Find and replace")
        plan = self._plan
        self._run_copies = copies
        # Off the UI thread. NO Cancel here on purpose: execute_plan is
        # two-phase (temporary names, then final names) and stopping between
        # the phases is the one state the undo log cannot describe.
        # Every receiver below is a bound method of this QWidget, so Qt
        # queues the call onto the UI thread; a bare lambda has no owning
        # QObject and its thread affinity is not something to rely on.
        folder = self.folder_row.path()
        worker = _BatchWorker([plan], lambda pl: execute_plan(
            pl, progress=worker.progress.emit, rule_label=label), self,
            path_of=lambda _pl: folder)
        self._worker = worker
        worker.progress.connect(self._on_rename_progress)
        worker.item_done.connect(self._on_rename_done)
        worker.item_failed.connect(self._on_rename_failed)
        worker.finished_all.connect(self._on_rename_finished)
        worker.start()

    def _on_rename_progress(self, done: int, total: int):
        self._set_status(f"Renaming... {done} of {total}", "busy")

    def stop_for_shutdown(self, wait_ms=60000):
        # Never abandon a two-phase rename mid-flight: wait for it, longer
        # than the other tabs, rather than cancelling between its phases.
        self._shutting_down = True
        if self._worker is None:
            return True
        return self._worker.wait(wait_ms)

    def _on_rename_done(self, _i, _plan, result):
        copies = self._run_copies
        renamed, failures, _log = result
        if failures:
            self._set_status("Completed with errors.", "error")
            self._notify("warning", "Completed With Errors",
                         f"Renamed: {renamed}\nFailed: {len(failures)}\n\n"
                         + "\n".join(failures))
        else:
            did = "Copied and renamed" if copies else "Renamed"
            self._set_status(f"{did} {plural(renamed)}.", "ok")
            self._notify("information", "Success", f"{did} {plural(renamed)}.")

    def _on_rename_failed(self, _i, _plan, message):
        self._set_status("Rename failed.", "error")
        self._notify("critical", "Error", message)

    def _on_rename_finished(self, _cancelled):
        self._busy = False
        self._worker = None
        self._sync_undo_button()
        self.rescan()

    def execute_undo(self):
        if self._busy:
            return
        confirm = Msg.question(
            self, "Undo Rename",
            "Restore the original filenames from the last rename?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        self._busy = True
        self.rename_btn.setEnabled(False)
        self.undo_btn.setEnabled(False)
        self._set_status("Undoing...", "busy")
        self._worker = _BatchWorker([None], lambda _x: undo(), self)
        self._worker.item_done.connect(self._on_undo_done)
        self._worker.item_failed.connect(self._on_undo_failed)
        self._worker.finished_all.connect(self._on_rename_finished)
        self._worker.start()

    def _on_undo_done(self, _i, _x, result):
        restored, failures = result
        if failures:
            self._set_status("Undo completed with errors.", "error")
            self._notify("warning", "Undo Completed With Errors",
                         f"Restored: {restored}\nFailed: {len(failures)}\n\n"
                         + "\n".join(failures))
        else:
            self._set_status(f"Restored {plural(restored)}.", "ok")
            self._notify("information", "Undo Complete", f"Restored {plural(restored)}.")

    def _on_undo_failed(self, _i, _x, message):
        self._set_status("Undo failed.", "error")
        self._notify("critical", "Error", message)

    def _on_cell_clicked(self, row: int, _col: int):
        """Peek at whichever file was clicked. Read-only and cheap."""
        item = self.table.item(row, 0)
        path = item.data(Qt.UserRole) if item else None
        if not path:
            return
        self.peek_label.setText(format_peek(peek_file(path)))

    def _sync_undo_button(self):
        self.undo_btn.setEnabled(os.path.exists(undo_log_path()) and not self._busy)

    def _set_status(self, text: str, kind: str = ""):
        """kind: "" | "ok" | "error" | "busy" — resolved by the stylesheet,
        so status colours follow the active theme instead of being frozen
        into an inline stylesheet at call time."""
        self.status.setText(text)
        self.status.setProperty("kind", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


class Collapsible(QWidget):
    """A titled section that starts closed. Long reference material should be
    available without pushing the short answers off the screen."""

    def __init__(self, title: str, body: QWidget, start_open: bool = False):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._body = body
        self.toggle = QPushButton()
        self.toggle.setObjectName("secondary")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(start_open)
        self._title = title
        self.toggle.clicked.connect(self._sync)
        lay.addWidget(self.toggle)
        lay.addWidget(body)
        self._sync()

    def _sync(self):
        open_ = self.toggle.isChecked()
        self.toggle.setText(("\u25be  " if open_ else "\u25b8  ") + self._title)
        self._body.setVisible(open_)


class SettingsPanel(QWidget):
    """Appearance, presets, and the regex reference. Split out of Help once it
    outgrew being a strip at the top of an FAQ."""

    REGEX_ROWS = [
        (".", "Any single character", "A.C matches ABC, A7C, A-C"),
        ("\\d", "Any digit 0-9", "ABC_\\d\\d\\d\\d matches ABC_1001"),
        ("\\w", "A letter, digit or underscore", "\\w+ matches Smith_1"),
        ("\\s", "A space or tab", "Smith\\sv\\sJones matches Smith v Jones"),
        ("*", "Zero or more of what came before", "AB*C matches AC, ABC, ABBC"),
        ("+", "One or more of what came before", "\\d+ matches 7 or 12345"),
        ("?", "Optional \u2014 zero or one", "colou?r matches color and colour"),
        ("^", "Start of the name", "^ABC matches only names starting ABC"),
        ("$", "End of the name", "Final$ matches only names ending Final"),
        ("[abc]", "Any one of these characters", "[AB]C matches AC and BC"),
        ("[^abc]", "Any character EXCEPT these", "[^0-9] matches anything not a digit"),
        ("[0-9]", "Any character in the range", "[A-Z] matches one capital letter"),
        ("(  )", "A group you can reuse in the replacement", "(\\w+)_(\\w+)"),
        ("\\1 \\2", "In Replace: reuse group 1, group 2", "Replace \\2_\\1 swaps them"),
        ("|", "Either / or", "Invoice|Receipt matches either word"),
        ("\\.", "A literal dot (backslash escapes it)", "\\.pdf$ matches the extension"),
        ("(a+)+  (a|b)*", "Refused: a repeated group that repeats or holds an 'or'",
         "These can freeze the program. Write [ab]+ instead of (a|b)+"),
    ]

    RECIPES = [
        ("Remove a prefix from every name",
         "Search: ^ABC_    Replace: (leave empty)    Regex ON",
         "ABC_1001.pdf  \u2192  1001.pdf"),
        ("Swap two parts around an underscore",
         "Search: (\\w+)_(\\w+)    Replace: \\2_\\1    Regex ON",
         "Smith_Jones.pdf  \u2192  Jones_Smith.pdf"),
        ("Keep only the file number at the start",
         "Search: ^(\\d+).*    Replace: \\1    Regex ON",
         "1001 Invoice Final.pdf  \u2192  1001.pdf"),
        ("Add a word to the end of every name",
         "Search: $    Replace: _FINAL    Regex ON",
         "1001.pdf  \u2192  1001_FINAL.pdf"),
        ("Replace spaces with underscores",
         "Search: \\s+    Replace: _    Regex ON",
         "Smith v Jones.pdf  \u2192  Smith_v_Jones.pdf"),
        ("Change just the extension",
         "Apply to: Extension only    Search: .PDF    Replace: .pdf",
         "SCAN.PDF  \u2192  SCAN.pdf"),
    ]

    def __init__(self, on_theme_change=None, on_layout_change=None):
        super().__init__()
        self._on_theme_change = _weak(on_theme_change)
        self._on_layout_change = _weak(on_layout_change)

        page = QVBoxLayout(self)
        page.setContentsMargins(28, 22, 28, 14)
        page.setSpacing(10)

        header = QLabel("Settings")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        page.addWidget(header)

        # Everything below scrolls: with the regex reference expanded this
        # column is taller than the window's minimum height.
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(0, 10, 8, 0)      # +10: same title-to-first-section gap as the other tabs
        outer.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        scroll.setFrameShape(QFrame.NoFrame)
        page.addWidget(scroll, stretch=1)

        set_title = QLabel("APPEARANCE")
        set_title.setObjectName("sectionTitle")
        outer.addWidget(set_title)

        set_panel = QFrame()
        set_panel.setObjectName("panel")
        sg = QGridLayout(set_panel)
        sg.setContentsMargins(16, 12, 16, 12)
        sg.setHorizontalSpacing(14)
        sg.setVerticalSpacing(6)
        sg.setColumnStretch(2, 1)

        # Radio rows, not dropdowns: every option visible, nothing to open.
        state = load_ui_state()

        def row_of(radios):
            host = QWidget()
            hl = QHBoxLayout(host)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(18)
            for rb in radios:
                hl.addWidget(rb)
            hl.addStretch(1)
            return host

        current_theme = state.get("theme", DEFAULT_THEME)
        if current_theme not in THEMES:
            current_theme = DEFAULT_THEME
        self.theme_group = QButtonGroup(self)
        self.theme_buttons = {}
        for name in THEMES:
            rb = QRadioButton(name)
            rb.setChecked(name == current_theme)
            self.theme_group.addButton(rb)
            self.theme_buttons[name] = rb
        sg.addWidget(QLabel("Theme"), 0, 0)
        sg.addWidget(row_of(self.theme_buttons.values()), 0, 1, 1, 3)

        current_size = state.get("text_size", DEFAULT_TEXT_SIZE)
        if current_size not in TEXT_SIZES:
            current_size = DEFAULT_TEXT_SIZE
        self.size_group = QButtonGroup(self)
        self.size_buttons = {}
        for name in TEXT_SIZES:
            rb = QRadioButton(name)
            rb.setChecked(name == current_size)
            self.size_group.addButton(rb)
            self.size_buttons[name] = rb
        sg.addWidget(QLabel("Text size"), 1, 0)
        sg.addWidget(row_of(self.size_buttons.values()), 1, 1, 1, 3)

        self.mascot_box = QCheckBox("Show Pagey, the paper mascot, in confirmation and result boxes")
        self.mascot_box.setChecked(bool(setting("mascot_dialogs")))
        sg.addWidget(QLabel("Dialogs"), 2, 0)
        sg.addWidget(self.mascot_box, 2, 1, 1, 3)

        self.side_group = QButtonGroup(self)
        self.side_buttons = {}
        for key, text, tip in (
                ("right", "Right", "Put the Actions column on the right of each tool (the default)"),
                ("left", "Left", "Put the Actions column on the left of each tool")):
            rb = QRadioButton(text)
            rb.setToolTip(tip)
            rb.setChecked(key == setting("actions_side"))
            self.side_group.addButton(rb)
            self.side_buttons[key] = rb
        self.collapse_box = QCheckBox("Collapse it to a thin strip (Ctrl+J)")
        self.collapse_box.setToolTip("Give the work area the full width; click the strip or press Ctrl+J to bring the Actions back")
        self.collapse_box.setChecked(bool(setting("actions_collapsed")))
        sg.addWidget(QLabel("Actions column"), 3, 0)
        sg.addWidget(row_of(list(self.side_buttons.values()) + [self.collapse_box]), 3, 1, 1, 3)

        self.launch_full_box = QCheckBox("Open a PDF from Windows full screen, in the Page Organizer's reader")
        self.launch_full_box.setToolTip(
            "On by default. Double-clicking a PDF (or Open with) maximizes the window and tucks the file tray away "
            "for that session. Turn it off to keep the window as it is.")
        self.launch_full_box.setChecked(bool(setting("launch_reader_full")))
        self.default_pdf_btn = QPushButton("Make Acro-Data my PDF app…")
        self.default_pdf_btn.setObjectName("secondary")
        self.default_pdf_btn.setToolTip("Register Acro-Data for your account, then open Windows' Default apps page for the final click")
        self.guide_btn = QPushButton("Run the setup guide again…")
        self.guide_btn.setObjectName("secondary")
        self.guide_btn.setToolTip("Open the welcome guide: look, Actions side and default PDF app")
        sg.addWidget(QLabel("PDFs from Windows"), 5, 0)
        sg.addWidget(self.launch_full_box, 5, 1, 1, 3)
        sg.addWidget(self.default_pdf_btn, 6, 1, 1, 2)
        sg.addWidget(self.guide_btn, 6, 3, 1, 1)

        self.long_paths_box = QCheckBox("Allow paths over 260 characters")
        self.long_paths_box.setToolTip(
            "Off by default. Windows can accept longer paths, but Acrobat, "
            "Explorer and upload tools often cannot open them.")
        self.long_paths_box.setChecked(bool(state.get("allow_long_paths")))
        sg.addWidget(QLabel("File names"), 4, 0)
        sg.addWidget(self.long_paths_box, 4, 1, 1, 3)
        outer.addWidget(set_panel)

        state["theme"] = current_theme
        state["text_size"] = current_size
        save_ui_state(state)
        for rb in list(self.theme_buttons.values()) + list(self.size_buttons.values()):
            rb.toggled.connect(self._radio_toggled)
        self.long_paths_box.toggled.connect(self._long_paths_toggled)
        self.mascot_box.toggled.connect(
            lambda on: save_settings({"mascot_dialogs": bool(on)}))
        self.launch_full_box.toggled.connect(
            lambda on: save_settings({"launch_reader_full": bool(on)}))
        self.default_pdf_btn.clicked.connect(self._make_default_pdf)
        self.guide_btn.clicked.connect(_later(self._run_guide))
        _ACTIONS_LISTENERS.add(self)
        for key, rb in self.side_buttons.items():
            rb.toggled.connect(_later(self._side_toggled, key))
        self.collapse_box.toggled.connect(self._collapse_toggled)

        opt_title = QLabel("PDF OPTIMIZER")
        opt_title.setObjectName("sectionTitle")
        outer.addWidget(opt_title)

        opt_panel = QFrame()
        opt_panel.setObjectName("panel")
        og = QGridLayout(opt_panel)
        og.setContentsMargins(16, 12, 16, 12)
        og.setHorizontalSpacing(14)
        og.setVerticalSpacing(8)
        og.setColumnStretch(2, 1)

        self.opt_open_box = QCheckBox("Open the Optimized folder when a run finishes")
        self.opt_open_box.setChecked(bool(setting("optimizer_open_when_done")))
        self.opt_flat_box = QCheckBox(
            "Allow turning pages into pictures as a last resort when a file still will not fit")
        self.opt_flat_box.setChecked(bool(setting("optimizer_allow_flatten")))
        self.opt_flat_box.setToolTip(
            "Off: a file that cannot be shrunk enough by removing hidden "
            "information and compressing scans is reported as 'Still too big' "
            "and keeps its text layer, instead of being flattened.")
        self.opt_dpi = QSpinBox()
        self.opt_dpi.setRange(72, 300)
        self.opt_dpi.setSingleStep(12)
        self.opt_dpi.setValue(int(setting("optimizer_flatten_dpi")))
        self.opt_dpi.setSuffix(" DPI")
        self.opt_dpi.setFixedWidth(116)
        self.opt_dpi.setAccessibleName("Resolution used when pages become pictures")
        dpi_hint = QLabel("Used by the last-resort flatten and by 'Flatten pages to "
                          "images'. Higher is sharper and larger; 120 is the default.")
        dpi_hint.setObjectName("hint")
        dpi_hint.setWordWrap(True)
        og.addWidget(QLabel("When done"), 0, 0)
        og.addWidget(self.opt_open_box, 0, 1, 1, 2)
        og.addWidget(QLabel("Flatten"), 1, 0)
        og.addWidget(self.opt_flat_box, 1, 1, 1, 2)
        og.addWidget(QLabel("Flatten quality"), 2, 0)
        og.addWidget(self.opt_dpi, 2, 1)
        og.addWidget(dpi_hint, 2, 2)
        outer.addWidget(opt_panel)
        self.opt_open_box.toggled.connect(
            lambda on: save_settings({"optimizer_open_when_done": bool(on)}))
        self.opt_flat_box.toggled.connect(
            lambda on: save_settings({"optimizer_allow_flatten": bool(on)}))
        self.opt_dpi.valueChanged.connect(
            lambda v: save_settings({"optimizer_flatten_dpi": int(v)}))

        pre_title = QLabel("RENAME PRESETS")
        pre_title.setObjectName("sectionTitle")
        outer.addWidget(pre_title)

        pre_panel = QFrame()
        pre_panel.setObjectName("panel")
        pl = QHBoxLayout(pre_panel)
        pl.setContentsMargins(14, 10, 14, 10)
        self.preset_count = QLabel()
        self.preset_count.setObjectName("hint")
        pl.addWidget(self.preset_count)
        pl.addStretch(1)
        exp = QPushButton("Export Presets\u2026")
        exp.setObjectName("secondary")
        exp.clicked.connect(self.export_presets_dialog)
        pl.addWidget(exp)
        imp = QPushButton("Import Presets\u2026")
        imp.setObjectName("secondary")
        imp.clicked.connect(self.import_presets_dialog)
        pl.addWidget(imp)
        outer.addWidget(pre_panel)

        # ---- shared profiles (administrator setting) ----------------------
        sh_title = QLabel("SHARED PROFILE LIST")
        sh_title.setObjectName("sectionTitle")
        outer.addWidget(sh_title)

        sh_panel = QFrame()
        sh_panel.setObjectName("panel")
        shl = QVBoxLayout(sh_panel)
        sh_hint = QLabel(
            "Point every machine at one profile file on the shared drive and a "
            "corrected rule reaches everyone by replacing that one file. "
            "Profiles from it are the same everywhere and cannot be edited on "
            "individual machines. Anything made on this machine still works "
            "and is kept separately.")
        sh_hint.setObjectName("hint")
        sh_hint.setWordWrap(True)
        shl.addWidget(sh_hint)

        sh_row = QHBoxLayout()
        self.shared_edit = QLineEdit(shared_profiles_path())
        self.shared_edit.setPlaceholderText(
            r"\\server\share\AcroD\profiles.json   "
            r"- leave empty to use this machine only")
        self.shared_edit.editingFinished.connect(self._save_shared_path)
        sh_row.addWidget(self.shared_edit, stretch=1)
        sh_browse = QPushButton("Browse...")
        sh_browse.setObjectName("secondary")
        sh_browse.clicked.connect(self._browse_shared)
        sh_row.addWidget(sh_browse)
        sh_clear = QPushButton("Clear")
        sh_clear.setObjectName("secondary")
        sh_clear.clicked.connect(self._clear_shared)
        sh_row.addWidget(sh_clear)
        shl.addLayout(sh_row)

        self.shared_status = QLabel("")
        self.shared_status.setObjectName("status")
        self.shared_status.setWordWrap(True)
        shl.addWidget(self.shared_status)
        outer.addWidget(sh_panel)

        loc_title = QLabel("WHERE YOUR SETTINGS ARE KEPT")
        loc_title.setObjectName("sectionTitle")
        outer.addWidget(loc_title)
        loc = QLabel(
            "Presets, saved layout, theme and the undo record all live in:\n" + _log_dir()
            + "\n\nThis folder survives program updates. Copy it to move your "
              "setup to another computer, or use Export Presets above."
        )
        loc.setObjectName("faqA")
        loc.setWordWrap(True)
        loc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(loc)

        rx_body = QWidget()
        rl = QVBoxLayout(rx_body)
        rl.setContentsMargins(0, 6, 0, 6)
        rl.setSpacing(8)

        intro = QLabel(
            "Leave 'Use regular expressions' switched OFF on the Bulk Renamer "
            "tab and everything you type is matched literally \u2014 a dot means a "
            "dot. Switch it ON and the characters below take on special "
            "meanings. Either way the preview shows the result on every file "
            "before anything is renamed, and problems block the button."
        )
        intro.setObjectName("faqA")
        intro.setWordWrap(True)
        rl.addWidget(intro)

        rec_title = QLabel("COMMON RECIPES \u2014 COPY THESE")
        rec_title.setObjectName("sectionTitle")
        rl.addWidget(rec_title)
        rec = QTableWidget(len(self.RECIPES), 3)
        rec.setToolTip("Ready-made find-and-replace recipes; copy one into the Find box")
        rec.setHorizontalHeaderLabels(["Goal", "Settings", "Example"])
        rec.verticalHeader().setVisible(False)
        rec.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rec.setAlternatingRowColors(True)
        rec.setShowGrid(False)
        for i, (g, st_, ex) in enumerate(self.RECIPES):
            for c, txt in enumerate((g, st_, ex)):
                rec.setItem(i, c, QTableWidgetItem(txt))
        rh = rec.horizontalHeader()
        for c in range(3):
            rh.setSectionResizeMode(c, QHeaderView.Stretch)
        rec.setFixedHeight(30 + 26 * len(self.RECIPES))
        rl.addWidget(rec)

        cs_title = QLabel("CHEAT SHEET")
        cs_title.setObjectName("sectionTitle")
        rl.addWidget(cs_title)
        cs = QTableWidget(len(self.REGEX_ROWS), 3)
        cs.setToolTip("Regular-expression cheat sheet")
        cs.setHorizontalHeaderLabels(["Symbol", "What it means", "Example"])
        cs.verticalHeader().setVisible(False)
        cs.setEditTriggers(QAbstractItemView.NoEditTriggers)
        cs.setAlternatingRowColors(True)
        cs.setShowGrid(False)
        for i, (sym, mean, ex) in enumerate(self.REGEX_ROWS):
            for c, txt in enumerate((sym, mean, ex)):
                cs.setItem(i, c, QTableWidgetItem(txt))
        ch = cs.horizontalHeader()
        ch.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        ch.setSectionResizeMode(1, QHeaderView.Stretch)
        ch.setSectionResizeMode(2, QHeaderView.Stretch)
        cs.setFixedHeight(30 + 26 * len(self.REGEX_ROWS))
        rl.addWidget(cs)

        warn = QLabel(
            "One trap worth knowing: typing  .*  on its own means 'the whole "
            "name'. This program applies it once; many other tools apply it "
            "twice and silently double your replacement."
        )
        warn.setObjectName("faqA")
        warn.setWordWrap(True)
        rl.addWidget(warn)

        self.regex_section = Collapsible(
            "Regular expressions \u2014 recipes and cheat sheet", rx_body, start_open=False)
        outer.addWidget(self.regex_section)

        self.advanced_section = Collapsible(
            "Advanced \u2014 folder names, suffixes, backups, undo depth, size-limit presets",
            self._build_advanced(), start_open=False)
        outer.addWidget(self.advanced_section)

        outer.addStretch(1)
        self._refresh_preset_count()

    # ---- advanced -------------------------------------------------------
    TAB_NAMES = ("PDF Merger", "PDF Splitter", "PDF Optimizer", "Page Organizer", "Bulk Renamer",
                 "Bulk File Mover", "Settings", "Help")

    def _build_advanced(self) -> QWidget:
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(8)

        intro = QLabel(
            "Defaults match how the program has always behaved; leave anything "
            "you do not need. Changes apply to the next job, not one already "
            "running.")
        intro.setObjectName("faqA")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.adv = {}

        def line(key, label, width=180, placeholder=""):
            w = QLineEdit(str(setting(key)))
            w.setFixedWidth(width)
            w.setPlaceholderText(placeholder)
            w.setAccessibleName(label)
            self.adv[key] = w
            form.addRow(label, w)

        def spin(key, label, lo, hi):
            w = QSpinBox()
            w.setRange(lo, hi)
            w.setValue(int(setting(key)))
            w.setFixedWidth(90)
            w.setAccessibleName(label)
            self.adv[key] = w
            form.addRow(label, w)

        line("split_dir_name", "Splitter output folder name:")
        line("cover_suffix", "Splitter cover-part suffix:")
        line("remaining_suffix", "Splitter remaining-part suffix:")
        line("optimized_dir_name", "Optimizer output folder name:")
        line("organized_dir_name", "Page Organizer output folder name:")
        spin("organizer_thumb_size", "Page Organizer thumbnail width (pixels):", 100, 260)
        spin("organizer_undo_depth", "Page Organizer page changes that can be undone:", 5, 200)
        refresh_combo = QComboBox()
        for _val, _label in AUTO_REFRESH_CHOICES:
            refresh_combo.addItem(_label)
        refresh_combo.setCurrentIndex([v for v, _l in AUTO_REFRESH_CHOICES].index(setting("organizer_auto_refresh")))
        refresh_combo.setFixedWidth(260)
        refresh_combo.setAccessibleName("Page Organizer list refresh")
        self.adv["organizer_auto_refresh"] = refresh_combo
        form.addRow("Page Organizer list refresh:", refresh_combo)
        zoom_combo = QComboBox()
        for _val, _label in READER_ZOOM_CHOICES:
            zoom_combo.addItem(_label)
        zoom_combo.setCurrentIndex([v for v, _l in READER_ZOOM_CHOICES].index(setting("reader_default_zoom")))
        zoom_combo.setFixedWidth(260)
        zoom_combo.setAccessibleName("Reader starting zoom")
        self.adv["reader_default_zoom"] = zoom_combo
        form.addRow("Reader starting zoom:", zoom_combo)
        spin("reader_cache_mb", "Reader memory for drawn pages (MB):", 64, 1024)
        ext_box = QCheckBox("Extract starts with “Delete these pages from the PDF after extracting” ticked")
        ext_box.setChecked(bool(setting("organizer_extract_delete_default")))
        ext_box.setAccessibleName("Extract starts with delete after extracting ticked")
        self.adv["organizer_extract_delete_default"] = ext_box
        form.addRow("", ext_box)
        rep_box = QCheckBox("Page Organizer: replace the original (a copy is kept in an Originals folder)")
        rep_box.setChecked(bool(setting("organizer_replace_original")))
        rep_box.setAccessibleName("Page Organizer replaces the original file")
        self.adv["organizer_replace_original"] = rep_box
        form.addRow("", rep_box)
        keep_box = QCheckBox("Page Organizer: keep this file's own scripts and programs when saving "
                             "(off = they are left out; pasted pages are always cleaned)")
        keep_box.setChecked(bool(setting("organizer_keep_scripts")))
        keep_box.setAccessibleName("Page Organizer keeps the file's own scripts and programs")
        self.adv["organizer_keep_scripts"] = keep_box
        form.addRow("", keep_box)
        spin("merge_backups", "Merger backups kept (copies of overwritten files):", 1, 10)
        spin("undo_depth", "Undo depth (rename and move runs that can be undone):", 1, 20)

        combo = QComboBox()
        combo.addItem("The tab I was on last time")
        for name in self.TAB_NAMES:
            combo.addItem(name)
        combo.setCurrentIndex(int(setting("default_tab")) + 1)
        combo.setFixedWidth(260)
        combo.setAccessibleName("Tab to open on launch")
        self.adv["default_tab"] = combo
        form.addRow("Open on launch:", combo)

        lay_combo = QComboBox()
        lay_combo.addItem("Tray: file tray and tool buttons", "tray")
        lay_combo.addItem("Classic: one tab per tool", "classic")
        lay_combo.setCurrentIndex(0 if setting("ui_layout") == "tray" else 1)
        lay_combo.setFixedWidth(260)
        lay_combo.setAccessibleName("Window layout")
        lay_combo.setToolTip("Tray puts a file tray beside the tools. Classic is the original tab strip. Applies when you press Save.")
        self.adv["ui_layout"] = lay_combo
        form.addRow("Layout:", lay_combo)

        line("mover_separators", "Mover: characters allowed after a file number:",
             width=120, placeholder="blank = any")
        sep_hint = QLabel("Blank means any character that is not a letter or digit "
                          "(so 123 matches 123-Jones.pdf and 123 Jones.pdf but never "
                          "1234.pdf). Type the characters to restrict it, e.g.  -_")
        sep_hint.setObjectName("faqA")
        sep_hint.setWordWrap(True)
        form.addRow("", sep_hint)

        presets = QPlainTextEdit()
        presets.setPlainText("\n".join(f"{n} = {mb:g}" for n, mb in setting("ceiling_presets")))
        presets.setPlaceholderText("Upload portal = 30\nEmail = 10")
        presets.setFixedHeight(90)
        presets.setAccessibleName("Optimizer size-limit presets, one per line, name = MB")
        self.adv["ceiling_presets"] = presets
        form.addRow("Optimizer size-limit presets\n(one per line: name = MB):", presets)
        lay.addLayout(form)

        row = QHBoxLayout()
        save = QPushButton("Save Advanced Settings")
        save.setObjectName("primaryAction")
        save.clicked.connect(self._save_advanced)
        row.addWidget(save)
        reset = QPushButton("Reset to Defaults")
        reset.setObjectName("secondary")
        reset.clicked.connect(self._reset_advanced)
        row.addWidget(reset)
        row.addStretch(1)
        lay.addLayout(row)

        self.adv_status = QLabel("")
        self.adv_status.setObjectName("status")
        self.adv_status.setWordWrap(True)
        lay.addWidget(self.adv_status)
        return body

    def _read_advanced(self):
        """Widget values -> (changes dict, list of human-readable problems)."""
        changes, problems = {}, []
        for key in ("split_dir_name", "cover_suffix", "remaining_suffix",
                    "optimized_dir_name", "organized_dir_name"):
            val = self.adv[key].text()
            if not valid_name_component(val):
                problems.append(f"'{val}' is not usable as a file or folder name.")
            elif key == "organized_dir_name" and val.strip().lower() == "originals":
                problems.append("'Originals' is kept for backups of replaced files; "
                                "choose another Page Organizer folder name.")
            changes[key] = val
        changes["merge_backups"] = self.adv["merge_backups"].value()
        changes["undo_depth"] = self.adv["undo_depth"].value()
        changes["organizer_thumb_size"] = self.adv["organizer_thumb_size"].value()
        changes["organizer_undo_depth"] = self.adv["organizer_undo_depth"].value()
        changes["organizer_auto_refresh"] = \
            AUTO_REFRESH_CHOICES[self.adv["organizer_auto_refresh"].currentIndex()][0]
        changes["reader_default_zoom"] = \
            READER_ZOOM_CHOICES[self.adv["reader_default_zoom"].currentIndex()][0]
        changes["reader_cache_mb"] = self.adv["reader_cache_mb"].value()
        changes["organizer_extract_delete_default"] = \
            self.adv["organizer_extract_delete_default"].isChecked()
        changes["organizer_replace_original"] = self.adv["organizer_replace_original"].isChecked()
        changes["organizer_keep_scripts"] = self.adv["organizer_keep_scripts"].isChecked()
        changes["default_tab"] = self.adv["default_tab"].currentIndex() - 1
        changes["ui_layout"] = self.adv["ui_layout"].currentData()
        seps = self.adv["mover_separators"].text()
        if any(c.isalnum() for c in seps):
            problems.append("Separators cannot include letters or digits.")
        changes["mover_separators"] = seps
        rows = []
        for ln in self.adv["ceiling_presets"].toPlainText().splitlines():
            if not ln.strip():
                continue
            name, sep, mb = ln.partition("=")
            try:
                mbv = float(mb.strip())
            except ValueError:
                mbv = 0
            if not sep or not name.strip() or not (0 < mbv <= 2000):
                problems.append(f"Preset line not understood: '{ln.strip()}' "
                                "(use  name = 6.9 ).")
                continue
            rows.append([name.strip(), mbv])
        changes["ceiling_presets"] = rows or list(ADVANCED_DEFAULTS["ceiling_presets"])
        return changes, problems

    def _save_advanced(self):
        changes, problems = self._read_advanced()
        if problems:
            self._set_kind(self.adv_status, " ".join(problems) + " Nothing was saved.", "error")
            return
        rejected = save_settings(changes)
        if rejected:
            self._set_kind(self.adv_status,
                           "Not saved: " + ", ".join(rejected), "error")
        else:
            self._set_kind(self.adv_status,
                           "Saved. Folder names and suffixes apply to the next job; "
                           "the launch tab applies next time the program opens.", "ok")
            if self._on_layout_change:
                self._on_layout_change()

    def _reset_advanced(self):
        save_settings(dict(ADVANCED_DEFAULTS))
        for key in ("split_dir_name", "cover_suffix", "remaining_suffix",
                    "optimized_dir_name", "organized_dir_name", "mover_separators"):
            self.adv[key].setText(str(ADVANCED_DEFAULTS[key]))
        self.adv["organizer_thumb_size"].setValue(ADVANCED_DEFAULTS["organizer_thumb_size"])
        self.adv["organizer_undo_depth"].setValue(ADVANCED_DEFAULTS["organizer_undo_depth"])
        self.adv["organizer_auto_refresh"].setCurrentIndex(
            [v for v, _l in AUTO_REFRESH_CHOICES].index(ADVANCED_DEFAULTS["organizer_auto_refresh"]))
        self.adv["reader_default_zoom"].setCurrentIndex(
            [v for v, _l in READER_ZOOM_CHOICES].index(ADVANCED_DEFAULTS["reader_default_zoom"]))
        self.adv["reader_cache_mb"].setValue(ADVANCED_DEFAULTS["reader_cache_mb"])
        self.adv["organizer_extract_delete_default"].setChecked(
            bool(ADVANCED_DEFAULTS["organizer_extract_delete_default"]))
        self.adv["organizer_replace_original"].setChecked(
            bool(ADVANCED_DEFAULTS["organizer_replace_original"]))
        self.adv["organizer_keep_scripts"].setChecked(bool(ADVANCED_DEFAULTS["organizer_keep_scripts"]))
        self.adv["merge_backups"].setValue(ADVANCED_DEFAULTS["merge_backups"])
        self.adv["undo_depth"].setValue(ADVANCED_DEFAULTS["undo_depth"])
        self.adv["default_tab"].setCurrentIndex(0)
        self.adv["ui_layout"].setCurrentIndex(0)
        if self._on_layout_change:
            self._on_layout_change()
        self.adv["ceiling_presets"].setPlainText(
            "\n".join(f"{n} = {mb:g}" for n, mb in ADVANCED_DEFAULTS["ceiling_presets"]))
        for box, key in ((self.mascot_box, "mascot_dialogs"),
                         (self.launch_full_box, "launch_reader_full"),
                         (self.opt_open_box, "optimizer_open_when_done"),
                         (self.opt_flat_box, "optimizer_allow_flatten")):
            box.setChecked(bool(ADVANCED_DEFAULTS[key]))
        self.opt_dpi.setValue(int(ADVANCED_DEFAULTS["optimizer_flatten_dpi"]))
        self.sync_actions_controls()
        refresh_inspectors()
        self._set_kind(self.adv_status, "Defaults restored.", "ok")

    def _side_toggled(self, key):
        if self.side_buttons[key].isChecked():
            save_settings({"actions_side": key})
            refresh_inspectors()

    def _collapse_toggled(self, on):
        save_settings({"actions_collapsed": bool(on)})
        refresh_inspectors()

    def _make_default_pdf(self):
        if not pdf_handler_available():
            self._set_kind(self.adv_status, "This works from the installed Windows program, not from source.", "")
            return
        ok, msg = register_pdf_handler()
        if ok:
            open_default_apps_page()
            msg += " On the Windows page that opened, choose Acro-Data for PDF."
        self._set_kind(self.adv_status, msg, "ok" if ok else "error")

    def _run_guide(self):
        fn = getattr(self.window(), "run_first_run_guide", None)
        if callable(fn):
            fn()
            self.sync_actions_controls()

    def sync_actions_controls(self):
        """Bring the Settings controls in line with the saved side / collapsed
        state (Ctrl+J or the strip's button changed it from outside)."""
        side = setting("actions_side")
        for key, rb in self.side_buttons.items():
            rb.blockSignals(True)
            rb.setChecked(key == side)
            rb.blockSignals(False)
        self.collapse_box.blockSignals(True)
        self.collapse_box.setChecked(bool(setting("actions_collapsed")))
        self.collapse_box.blockSignals(False)

    def _set_kind(self, label, text, kind):
        label.setText(text)
        label.setProperty("kind", kind)
        label.style().unpolish(label)
        label.style().polish(label)

    # ---- shared profile list ---------------------------------------------
    def _set_shared_status(self, text, kind=""):
        self.shared_status.setText(text)
        self.shared_status.setProperty("kind", kind)
        self.shared_status.style().unpolish(self.shared_status)
        self.shared_status.style().polish(self.shared_status)

    def _refresh_shared_status(self):
        path = shared_profiles_path()
        if not path:
            self._set_shared_status(
                "Not set. This machine uses its own profile list only.", "")
            return
        if not os.path.exists(path):
            self._set_shared_status(
                "That file cannot be found. Check this machine can reach the "
                "shared drive. The Bulk Renamer still works with this "
                "machine's own profiles.", "error")
            return
        profiles, err = _read_profiles_file(path)
        if err or not profiles:
            self._set_shared_status(
                f"That file could not be read ({err or 'no profiles in it'}).",
                "error")
            return
        names = ", ".join(p.name for p in profiles[:6])
        more = "" if len(profiles) <= 6 else f" and {len(profiles) - 6} more"
        ver = available_version()
        self._set_shared_status(
            f"{plural(len(profiles), 'shared profile')}: {names}{more}."
            + (f"  Version marker on the share: {ver}." if ver else ""), "ok")

    def _save_shared_path(self):
        if self.shared_edit.text().strip() == shared_profiles_path():
            return                      # editingFinished also fires on focus-out
        set_shared_profiles_path(self.shared_edit.text().strip())
        self._refresh_shared_status()
        Msg.information(
            self, "Shared profile list",
            "Saved. The Bulk Renamer picks this up the next time the app "
            "starts.")

    def _browse_shared(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the shared profile file", shared_profiles_path() or "",
            "Profile list (*.json)")
        if path:
            self.shared_edit.setText(path)
            self._save_shared_path()

    def _clear_shared(self):
        self.shared_edit.clear()
        set_shared_profiles_path("")
        self._refresh_shared_status()

    def current_theme_name(self) -> str:
        for name, rb in self.theme_buttons.items():
            if rb.isChecked():
                return name
        return DEFAULT_THEME

    def current_size_name(self) -> str:
        for name, rb in self.size_buttons.items():
            if rb.isChecked():
                return name
        return DEFAULT_TEXT_SIZE

    def showEvent(self, event):
        # The count goes stale the moment a preset is saved on the Renamer tab.
        super().showEvent(event)
        self._refresh_preset_count()

    def _refresh_preset_count(self):
        n = len(load_presets())
        self.preset_count.setText(
            "No presets saved yet \u2014 save one from the Bulk Renamer tab."
            if not n else f"{plural(n, 'preset')} saved."
        )

    def export_presets_dialog(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Presets", "acrod-rename-presets.json", "Preset files (*.json)")
        if not path:
            return
        try:
            n = export_presets(path)
        except (RenameExecError, OSError) as e:
            Msg.critical(self, "Export Failed", str(e))
            return
        Msg.information(
            self, "Presets Exported",
            f"{plural(n, 'preset')} written to:\n{os.path.basename(path)}")

    def import_presets_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Presets", "", "Preset files (*.json)")
        if not path:
            return
        overwrite = False
        try:
            with open(path, "r", encoding="utf-8") as f:
                incoming = (json.load(f) or {}).get("presets") or {}
            clash = sorted(set(incoming) & set(load_presets()))
        except Exception:
            clash = []
        if clash:
            choice = Msg.question(
                self, "Names Already In Use",
                f"{plural(len(clash), 'preset name')} already exist:\n"
                + ", ".join(clash[:6]) + ("\u2026" if len(clash) > 6 else "")
                + "\n\nReplace the existing ones?\n"
                  "Choosing No keeps both \u2014 imported copies get a suffix.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.No)
            if choice == QMessageBox.Cancel:
                return
            overwrite = (choice == QMessageBox.Yes)
        try:
            added, skipped, renamed = import_presets(path, overwrite=overwrite)
        except (RenameExecError, OSError) as e:
            Msg.critical(self, "Import Failed", str(e))
            return
        lines = [f"Imported: {added}"]
        if skipped:
            lines.append(f"Skipped (unreadable): {skipped}")
        if renamed:
            lines.append("Kept both, imported as: " + ", ".join(renamed[:6]))
        Msg.information(self, "Presets Imported", "\n".join(lines))
        self._refresh_preset_count()
        if self._on_theme_change:
            self._on_theme_change()

    def _long_paths_toggled(self, checked: bool):
        update_ui_state(allow_long_paths=bool(checked))

    def _radio_toggled(self, checked: bool):
        if checked:
            self._changed()

    def _changed(self, *_args):
        """Persist immediately, restyle on the next event-loop tick.

        app.setStyleSheet() costs ~77ms because it re-polishes every widget.
        Running it inside the signal handler lands it while the control is
        still redrawing itself; one tick later it is invisible.
        """
        theme = self.current_theme_name()
        size = self.current_size_name()
        update_ui_state(theme=theme, text_size=size)
        QTimer.singleShot(0, lambda: self._apply_now(theme, size))

    def _apply_now(self, theme: str, size: str):
        apply_theme(QApplication.instance(), theme, size)
        if self._on_theme_change:
            self._on_theme_change()


HELP_TAB_TITLES = ("PDF Merger", "PDF Splitter", "PDF Optimizer", "Page Organizer",
                   "Bulk Renamer", "Bulk File Mover", "Settings")


def help_tabs_mentioned(text: str, limit: int = 4):
    """The tabs a Help entry talks about, in the order they first appear. An entry that
    names more than `limit` of them is an overview, not about one place: no buttons."""
    low = text.lower()
    found = sorted((low.find(t.lower()), t) for t in HELP_TAB_TITLES if t.lower() in low)
    return [t for _pos, t in found] if len(found) <= limit else []


class _FaqAnswer(QLabel):
    """An answer plus the 'Go to <tab>' row under it: they show and hide together."""
    nav = None

    def setVisible(self, visible):
        super().setVisible(visible)
        if self.nav is not None:
            self.nav.setVisible(visible)


class _FaqQuestion(QLabel):
    """A question that opens and closes its answer on click, Enter or Space.
    It is focusable so a keyboard or screen-reader user can reach every answer."""

    def __init__(self, text, answer):
        super().__init__("")
        self._q, self._answer, self.expanded = text, answer, False
        self.setObjectName("faqQ")
        self.setWordWrap(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(text)
        self._sync()

    def _sync(self):
        self.setText(("\u25be  " if self.expanded else "\u25b8  ") + self._q)
        self.setToolTip("Click to hide the answer" if self.expanded else "Click to read the answer")

    def toggle(self):
        self.expanded = not self.expanded
        self._answer.setVisible(self.expanded)
        self._sync()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.toggle()
            event.accept()
        else:
            super().keyPressEvent(event)


class HelpPanel(QWidget):
    """FAQ, regex reference and ownership. Settings moved to its own tab."""

    FAQ = [
        ("What does each tab do?",
         "PDF Merger adds a reference PDF's pages onto a file you drop in. "
         "PDF Splitter separates the cover page or pages from the rest. "
         "PDF Optimizer shrinks a PDF and strips hidden information, or just reports what is in it. "
         "Page Organizer lets you open each PDF in a folder in turn, see its "
         "pages, reorder, turn, delete or extract them, and find text across "
         "the whole folder; double-click a PDF there to read it full size, like a PDF reader. "
         "Bulk Renamer renames many files at once using a rule. "
         "Bulk File Mover moves files by file number."),
        ("What is the tray, and how do I get the old tabs back?",
         "The tray is a waiting list for files. It starts hidden: press Show file tray (or Ctrl+T), or simply drag PDFs or a "
         "folder from Explorer onto the window and it opens by itself. Put files in it (drop them, or use Add files and "
         "Add folder), tick the ones you want, pick a tool along the top, and press the highlighted button under it (it is named for what it does, such as Merge these two or Optimize now). Double-click a file or "
         "folder in the tray to look at it in the Page Organizer; right-click a row for more (show in its folder, tick only "
         "that one, remove). Drag a row up or down to change the order, or drag it onto a tool's drop zone or into "
         "Explorer. To compare, drag a PDF from the Page Organizer's list, from the tray or from Explorer onto the B pane (or right-click it and choose Compare). "
         "Every tool's Send To menu also has File tray, to keep results for later. "
         "Remove or the Delete key only takes a row out of the tray; the file stays where it is. Nothing in the tray "
         "changes a file; it only remembers what you picked. Merge takes exactly two ticked PDFs (the first is updated "
         "with the pages of the second, with the usual confirmation and backup). Split and Optimize take every ticked PDF, "
         "Adding a folder lists the PDFs directly inside it under the folder row, so you can use them one by one at once: "
         "tick or untick single PDFs, or the folder row for all of them; removing the folder row removes its PDFs from the tray too (never from disk). "
         "Organize, Rename and Move take a folder. "
         "Dragging a ticked row carries every ticked PDF. Each tool still has its own file pickers and drop zone. "
         "To go back to one tab per tool, open Settings, Advanced, Layout, choose Classic and press Save; it applies at once."),
        ("How do I see a signature or small print in the Page Organizer?",
         "Hold Ctrl and turn the mouse wheel over the page pictures: up makes them bigger, down smaller. The + and \u2212 "
         "buttons under the pages and Ctrl+Plus, Ctrl+Minus and Ctrl+0 (back to normal) do the same. The pages you can "
         "see are redrawn sharper as you zoom. Open a PDF full size and the list of PDFs in the folder stays on the left "
         "(the Files button hides it)."),
        ("How do I type text onto a PDF in the Page Organizer?",
         "Open a PDF full size and press Text (or the T key). Click empty page and type; press Esc to finish. "
         "Drag a box to move it, drag its side handles to make it wider, double-click it to change the words. The bar "
         "above the pages sets the font, size, bold, italic, underline, colour and alignment. Right-click a box or the "
         "page for the same things in a menu. Delete removes a box, Ctrl+D or Ctrl+C then Ctrl+V copies it, "
         "the arrow keys nudge it, and Ctrl+Z takes the last change back. The PDF is saved as a NEW "
         "file ending in _edited; the original is never replaced."),
        ("Can I change words that are already on a PDF?",
         "Yes. With the Text tool on, click a line of the page's own text (it lights up as you point at it) and retype it; "
         "Alt+click takes the whole paragraph. Right-click it to delete a line or to add new text there instead. "
         "The new words use the nearest ordinary font (Arial, Times New Roman or Courier New), so they look like the old "
         "ones without being the very same font. When the PDF is saved the old words are taken out of the page, but only after "
         "the page is drawn before and after to prove nothing else changed. If that cannot be proved, or the words are a "
         "picture (a scan), the old words are hidden under a patch the colour of the paper and the save message says so: "
         "they are then still inside the file. For a scan, right-click, choose Cover and retype a region, drag around the "
         "words and type. Always save to a new _edited file, and check it, before relying on it."),
        ("What happens to a digital signature when I add text?",
         "Adding new text to a signed PDF asks you first. You can keep the signature (the text is added after it, the signed "
         "part is untouched, and PDF viewers will show that the file was changed after it was signed), or save a copy "
         "without the signature. Keeping it is not possible if you also moved, turned or removed pages, changed words that "
         "were already on a page, or the signer locked the file against changes."),
        ("The PDF Merger overwrote my file. Is that a bug?",
         "No \u2014 that is what it is for. The merger replaces the file you drop "
         "in with the combined version. It asks you to confirm first. If you "
         "need to keep the original, copy it somewhere else before dropping it."),
        ("Does the PDF Splitter destroy the original?",
         "No. The original is left exactly as it was. The two new files are "
         f"written into a folder named '{SPLIT_DIR_NAME}' created next to it, "
         "ending in _Cover and _Remaining (or _Last1, _Last2 or _Last3 and "
         "_Remaining when you choose to split off the last pages). Pick the "
         "front or the back with the two rows at the top of the tab, then "
         "1, 2 or 3 pages in that row's dropdown."),
        ("Does the Page Organizer change my PDF?",
         "Not unless you ask it to. Save & Next writes the corrected PDF into "
         "a folder named 'Reorganized' next to the original and moves on to the "
         "next PDF. The original is untouched. Extract is different: it always "
         "asks where to save the pages and what to call the file, and never "
         "writes anything until you press Extract. If you close the program or "
         "move to another PDF with unsaved changes it asks first. Settings > Advanced can "
         "switch on 'Replace the original', which first copies the original "
         "into an Originals folder inside it. If the PDF had bookmarks, the saved copy "
         "has none when pages were moved or deleted, because they would point "
         "at the wrong pages; you are told when that happens."),
        ("How do I open several PDFs from Windows at once?",
         "The easy way: Settings > \"Make Acro-Data my PDF app\" (or the setup guide on first start) registers Acro-Data and opens "
         "Windows' Default apps page, where you choose it for PDF. By hand: right-click a PDF, Open with, Choose another app, "
         "pick Acro-Data, tick Always. A PDF opened from Windows fills the screen in the reader (Settings can turn that off). Then select as many PDFs as you like in Explorer and press Enter: "
         "they all go to this one window, the first opens in the reader, and the rest wait in a list "
         "(up to 200). Next PDF and Previous PDF (Ctrl+Page Down and Ctrl+Page Up), or a click in the list, "
         "move between them; a check mark shows the ones you have opened, and Clear viewed tidies the list. "
         "If you have unsaved page changes you are asked before leaving a PDF; PDFs you open meanwhile just "
         "join the list. Saving writes a copy into the Reorganized folder and leaves the original alone, so "
         "its signatures stay intact. The list is not kept when you close the app, and you are asked "
         "first if PDFs are still waiting. Windows may ask you to confirm when you open about 15 or more files at once. "
         "Acro-Data shows signatures but does not check them; use Adobe for that."),
        ("How do I read a PDF full size in the Page Organizer?",
         "Double-click a PDF in the list (or select it and press Enter) to read it "
         "full size, or double-click one page in the grid to open the reader at that page. "
         "You see the pages exactly as they will be saved: moved pages are in their new "
         "place, turned pages are turned, deleted pages are gone. Scroll to read; Fit width, "
         "Fit page, the Zoom buttons, Ctrl+mouse wheel, Ctrl+Plus and Ctrl+Minus change the size; "
         "Ctrl+0 fits the page, Ctrl+1 is 100% and Ctrl+2 fits the width. Drag a page to move "
         "around when zoomed in. The page strip, the Page box (Ctrl+G) and the arrows beside it jump to a page. "
         "Click a page to select it (Ctrl or Shift for several; from the keyboard, Enter selects the "
         "page on screen and Shift+Down or Shift+Up adds more), then use Rotate left (Ctrl+Shift+L), "
         "Rotate right (Ctrl+Shift+R), Delete page (the Delete key; Backspace does nothing), Extract or right-click; "
         "with nothing selected they act on the page on screen. A dot and 'Unsaved changes' show that "
         "the file has not been written yet: Save & Next PDF (Ctrl+S) writes it. Undo (Ctrl+Z) works here too, because the reader and the grid "
         "are the same list of pages. Type a word in Find and press Enter to highlight it on every "
         "page (Enter or Shift+Enter move between matches); if you searched the folder first, the "
         "word is already there (Ctrl+F opens the Find bar; Esc closes it). Save & Next PDF and Skip keep you in the reader on the next PDF. "
         "Esc (or Back to pages) returns to the grid. If the file changes on disk while you read "
         "it, a bar says so; you keep seeing the version you opened. Settings > Advanced sets the "
         "reader's starting zoom and how much memory it may use for drawn pages."),
        ("How do I copy or move pages from one PDF into another?",
         "Click the pages, press Ctrl+C to copy or Ctrl+X to cut, open the other PDF "
         "in the list, click a page and press Ctrl+V to paste after it (Ctrl+Shift+V pastes "
         "before it; with no page selected it pastes at the end). Right-click a page for "
         "Cut, Copy and Paste, or right-click a PDF in the list to copy all its pages. "
         "You can also drag a PDF file (from the list, the file tray or Explorer) onto the pages: a gold bar "
         "shows where all its pages will go. A PDF copied in Explorer pastes the same way with Ctrl+V, and "
         "right-click > Insert pages from a PDF file picks one. One insert takes up to 300 pages. "
         "Cut pages stay where they are, faded, so you can see what is being moved; click where they should go and press Ctrl+V and the same pages move there "
         "(a Copy afterwards, or Undo, cancels the cut). Faded pages are left out when the PDF is saved. "
         "Nothing is written until you press Save & Next PDF, and Undo takes a paste or "
         "cut back. Pages keep their form fields, but saved copies leave out any scripts, launch actions and attached programs (links and page jumps still work); Settings can keep your own file's, and pasted pages are always cleaned. "
         "Pages pasted from another PDF can be read full size (a read-only view) right away; extract them after you save."),
        ("How do I compare two PDFs or pull pages from one into another side by side?",
         "In the Page Organizer press Compare to open a second pane (B) beside the PDF you are building (A). "
         "Choose any PDF for B from the folder list or Browse. Click pages in B, then drag them into A, "
         "double-click them, or use Insert after or Insert before; Copy puts them on the clipboard for Ctrl+V. "
         "Swap makes B the PDF you are building and shows A as the source (B must be in the folder list). "
         "B is only read and is never changed."),
        ("How do I undo a merge?",
         "After Merge into the first PDF finishes, the PDF Merger's Undo Last Merge button puts the first PDF back "
         "exactly as it was. It refuses, and says why, if the PDF was changed after the merge or the backup is "
         "missing, so it never overwrites newer work."),
        ("How do I send a result to another tab?",
         "The PDF Merger and PDF Splitter have a Send To button, and the Page Organizer's file menu has Send to. "
         "It opens the result in the PDF Splitter, PDF Optimizer, Page Organizer or Bulk Renamer without browsing for it again."),
        ("How do I move pages or use right-click in the Page Organizer?",
         "Drag a page (or several selected pages) and drop it where the gold "
         "bar shows; the others make room and every page stays on screen. "
         "Undo (or Ctrl+Z, or Ctrl+Shift+Z) takes back the last change to this PDF, and "
         "you can keep pressing it to go further back; opening another PDF "
         "starts a fresh history. You can also drop a folder or PDFs "
         "from Explorer onto the tab to open them. Right-click a page for "
         "Rotate, Delete, Move to start or end and Extract (the Delete key and "
         "the Delete page button do the same as Delete; Undo brings deleted "
         "pages back, and Reset brings back everything); right-click a PDF "
         "in the list to open it, skip it, open it in your viewer, show it in "
         "its folder or copy its path. Extract always asks where to save the "
         "pages and what to call the file; a name that already exists is "
         "numbered, never overwritten. Tick \"Delete these pages from the PDF "
         "after extracting\" in that box to move pages out instead of copying "
         "them: they leave the PDF you are organizing only once the new file is "
         "written, Undo brings them back, and the original changes only when you "
         "Save (the copy goes in Reorganized). It is unavailable when every page "
         "is selected, because a PDF needs at least one page."),
        ("Does the Page Organizer list update by itself?",
         "Yes. A PDF you extract into the folder on screen appears in the list straight away "
         "(marked +) so you can open it and extract from it again; Save & Next does not walk "
         "into it. The list also re-reads the folder when you come back to the window or tab, "
         "so PDFs added, removed or changed by other programs show up, and the Refresh button "
         "or F5 does it on demand. An open PDF that changes on disk is marked ! and will not be "
         "saved over; click it again to reload. Settings > Advanced > Page Organizer list refresh "
         "turns this off, or adds a check every 10 seconds. Extracts saved into the Reorganized "
         "folder are not listed, because subfolders are never listed."),
        ("Why did Find and replace refuse my regular expression?",
         "Patterns that repeat a group which already repeats, like (a+)+, or repeat "
         "a group holding an 'or', like (a|b)*, can make a computer work for minutes "
         "or hours on a single name, so the program refuses them before running "
         "anything. Almost always the inner + or * can be removed, or [ab]+ can "
         "replace (a|b)+. Everything else \u2014 look-arounds, back-references, "
         "long patterns \u2014 still works here. Auto Rename is stricter and also "
         "refuses back-references and look-arounds."),
        ("Why does a PDF say it is built to expand to an enormous size?",
         "Some files are tiny on disk but are made to unpack into gigabytes of data the "
         "moment a program reads them. That can freeze or close any PDF program, so "
         "searching, previewing, inspecting, optimizing and flattening refuse them "
         "before reading any further and name the file. It is a safety check, not a "
         "damaged file. Everything else in the folder carries on normally. If you "
         "trust where the file came from, open it in a PDF viewer instead."),
        ("Why does Open file not start a program?",
         "Right-click > Open file hands the file to the program Windows uses for it, "
         "which is what you want for PDFs, pictures and documents. For files that "
         "run code (.exe, .bat, .cmd, .ps1, .js, .vbs, .lnk, .msi and macro "
         "documents such as .docm) it shows the file in its folder instead, so a "
         "stray download in a batch cannot be started by accident. Double-click it "
         "in Explorer if you really mean to run it. In a saved results file, a file "
         "name that starts with = + - or @ is shown with a leading ' so a "
         "spreadsheet treats it as text, not a formula."),
        ("What does the Page Organizer search find?",
         "Text that is already inside the PDF, in every PDF directly in the "
         "folder. A scanned page with no text layer cannot be searched; those "
         "PDFs are marked 'no text'. There is no OCR in this tool."),
        ("Who is the little paper character in the message boxes?",
         "Pagey, a small ink-drawn sheet of paper with a paperclip. In message boxes it changes mood with the message: happy for a "
         "finished job, curious when it needs your OK, worried for a warning, "
         "alarmed for an error. Settings > "
         "Dialogs turns Pagey off and brings back the plain Windows "
         "message boxes."),
        ("Where are the buttons, and how do I move or hide the Actions column?",
         "Each tool keeps its buttons (Choose, Run again, Undo, Open folder, Send To) in the Actions column. It sits on the "
         "right. To give the work area more room, press the panel button at the top of the column or Ctrl+J; press the thin "
         "strip or Ctrl+J again to bring it back. Settings > Appearance > Actions column puts it on the left instead, or "
         "starts it collapsed. When the window is narrow the column folds to the strip by itself and opens again when you click it."),
        ("Why do some tabs show only an icon?",
         "The tab row always fits the window. When it is too narrow for every "
         "name, spacing tightens first; narrower still, only the tab you are on "
         "keeps its name and the others shrink to their icon (hover one to see "
         "its name). Widen the window, or use Ctrl+1 to Ctrl+8, to get the "
         "names back."),
        ("What is Auto Rename?",
         "The Bulk Renamer has two modes, each behind a heading you click to "
         "open. 'Find and replace' is the original: you type what to look for "
         "and what to put in its place. 'Auto Rename' renames by PROFILE \u2014 it "
         "reads the reference number off the front of the incoming filename, "
         "looks for keywords like 'motion' or 'order', and builds the new name "
         "from them. Only one mode is open at a time, so it is always clear "
         "which one the preview is showing."),
        ("How do I set up a profile in Auto Rename?",
         "Open Auto Rename, pick a profile, and edit the keyword list. Each row "
         "says 'if the name contains this, end the name with that'. Order "
         "matters: files are checked top to bottom and stop at the first match, "
         "so put the most specific keywords first. Use 'Try a filename' at the "
         "bottom to check a real example before you save. 'New...' adds a "
         "profile; 'Export...' sends one to a colleague as a small file."),
        ("Does Auto Rename change my original files?",
         "No, not by default. It leaves every original exactly where it is and "
         "puts a renamed COPY of each matching file into a 'Renamed' folder "
         "inside the folder you picked. Undo then simply deletes those copies. "
         "If you would rather the files were moved instead of copied, turn off "
         "'Keep the originals' under 'Naming and folder settings'."),
        ("Auto Rename skipped a file. Why?",
         "Either there was no reference number at the start of the name, or "
         "none of that profile's keywords appeared in it. The Status column says "
         "which. A skipped file is left completely alone and does not stop the "
         "rest of the batch."),
        ("Why does '.PDF' come out as '.pdf'?",
         "On purpose. Scans arrive as a mix of .PDF, .Pdf and .pdf, and "
         "anything that sorts or de-duplicates them treats those as three "
         "different things. Auto Rename makes the extension lower case so they "
         "all match. You can turn it off per profile under 'Naming and folder "
         "settings' if you need the old behaviour exactly."),
        ("Why is the Rename button greyed out?",
         "Either the rule changes nothing, or at least one file has a problem. "
         "Check the Status column. One bad file blocks the whole batch on "
         "purpose \u2014 a half-finished rename is worse than none."),
        ("Some rows in the preview are greyed out. Why?",
         "Those are the files your rule does NOT change. They are muted so the "
         "files that WILL be renamed stand out. Grey means nothing will happen "
         "to that file."),
        ("What do the Status messages mean?",
         "collision: two files would end up with the same name. "
         "already exists: something else is already using that name. "
         "reserved name: Windows forbids names like CON, PRN or NUL. "
         "path too long: the full path would exceed 260 characters. "
         "skipped: Auto Rename found no reference number or no keyword, so "
         "the file is left alone. "
         "invalid characters: the name contains < > : \" / \\ | ? * or ends "
         "with a space or dot."),
        ("It says a file is open in another program. What do I do?",
         "Close that program \u2014 usually Acrobat or a preview window \u2014 and run "
         "the rename again. The message names the program holding the file "
         "when Windows will tell us."),
        ("What is the preview panel under the file list?",
         "Click any row and it shows what is inside that file: its size, page "
         "count, and the first few lines of page one. That is usually enough "
         "to see the title and reference number without opening it."),
        ("How far back does Undo go?",
         "In the Bulk Renamer and the Bulk File Mover, Undo takes back the most "
         "recent run, and as many runs before it as 'Undo depth' allows in "
         "Settings > Advanced (1 unless you raise it). In the Page Organizer, "
         "Undo takes back changes to the PDF you have open, up to the limit in "
         "Settings > Advanced (100 unless you change it), until "
         "you open another PDF or save this one. The same page has a setting that makes "
         "the Extract box 'Delete these pages from the PDF after extracting' start ticked."),
        ("What does the PDF Optimizer actually do to my file?",
         "Two things. It shrinks the file - mostly by re-compressing the "
         "scanned page images - and it strips hidden information: document "
         "properties, embedded files, JavaScript, automatic actions, page "
         f"thumbnails and every earlier saved version. The result goes into an "
         f"'{OPTIMIZED_DIR_NAME}' folder next to the original. The original is "
         "never changed."),
        ("Will the Optimizer remove a stamp or the searchable text?",
         "Both survive steps 1 and 2. Nothing removes an annotation or the "
         "text layer while the file is only being cleaned or the scans "
         "shrunk. They are lost only if the file has to be flattened, which "
         "is the last resort and is named in the 'What it had to do' column - "
         "so you "
         "always know which kind of file you have. Keep your original either "
         "way."),
        ("The Optimizer says 'Still too big'. Now what?",
         "That is now rare, because the last step re-renders the pages as "
         "images and that always fits. If it still says so, the document is "
         "long enough that even flattened pages cannot make the size - split "
         "it and file it in parts. Click the row: the panel shows the page "
         "count and where the bytes are."),
        ("Why does the 'What it had to do' column sometimes say 'Flattened'?",
         "Because removing hidden information and shrinking the scans were "
         "not enough, so the pages were re-rendered as pictures. That always "
         "makes the size, but the result is no longer searchable and has no "
         "annotations, form fields or bookmarks. It is the last step, never "
         "the first, and your original is untouched - keep it."),
        ("What does the 'Flatten pages to images' tick box do?",
         "It forces every page to be re-rendered as a picture, and the "
         "maximum file size is NOT applied - the result can come out bigger "
         "than the limit, or bigger than the original. Use it when flattening "
         "is the point: killing a text layer, an unwanted form, or a file a "
         "viewer will not display properly. Left unticked, flattening still "
         "happens automatically, but only as the last resort and only when "
         "the file is over the limit. Either way the copy is not searchable "
         "and has no annotations, form fields or bookmarks, and your original "
         "is untouched."),
        ("Why are there no quality settings any more?",
         "Because the right answer never varied. The tool always tries the "
         "least damaging thing first and only escalates when the file is "
         "still too big, so a set of switches only offered ways to get a "
         "worse result. The size limit is the one thing that genuinely "
         "changes from job to job, so it is the one thing you set. The few "
         "things that are about your workflow rather than quality live on the "
         "Settings tab under PDF OPTIMIZER: open the Optimized folder when a "
         "run finishes, whether flattening to pictures is allowed as a last "
         "resort, and the DPI it uses."),
        ("The panel says most of the file is not image data. What does that mean?",
         "A PDF is not only its scans. Embedded fonts, the searchable text "
         "layer and page drawing commands all take space, and no amount of "
         "image compression touches them. If that figure is already larger "
         "than your size limit, compressing the scans can never get there - "
         "flattening is the only thing that removes it."),
        ("What happens if the PDF is digitally signed?",
         "It is optimized like any other file, and the row says SIGNATURE "
         "VOIDED in amber. Rewriting a PDF always voids its signature - that "
         "is true in Acrobat too - so the optimized COPY can no longer be "
         "verified as signed. Your original is untouched and still valid. If "
         "the signature has to hold, file the original; if the file simply "
         "has to meet a size limit, file the optimized copy. The warning is "
         "repeated in the summary at the end of the run and in the saved "
         "results file, so it cannot be missed later."),
        ("How does the Bulk File Mover decide which files to move?",
         "It matches the start of the file name against each number you "
         "type, up to a separator - so 123 matches 123-Smith.pdf and "
         "123_Jones.pdf but NOT 1234-Brown.pdf. It only ever moves files, "
         "never folders. Before it runs it shows you the list it matched and "
         "names any number that matched nothing."),
        ("The PDF Merger says the file is the same as the reference. Why?",
         "Because the file you dropped IS the reference PDF. Merging a "
         "document onto itself doubles it and destroys the original, so it "
         "is refused and nothing is changed. If you ever do overwrite the "
         "wrong file, a copy of how it looked beforehand is kept in the "
         "app's settings folder - the location is on the Settings tab."),
        ("I moved files to the wrong folder. Can I get them back?",
         "Yes. The Bulk File Mover keeps a record of the last move, and the "
         "'Undo Last Move' button puts those files back where they came "
         "from. It covers the most recent move only."),
        ("Can the Bulk Renamer rename folders?",
         "No. It only ever touches files. Folders are never listed and never "
         "renamed, by design."),
        ("Are there keyboard shortcuts?",
         "Yes. Ctrl+1 to Ctrl+8 switch tabs left to right. Ctrl+O chooses "
         "files or a folder on the current tab, Ctrl+Return runs its action "
         "(Rename, Move, Run again, Save & Next PDF), Ctrl+Z or Ctrl+Shift+Z undoes the last rename or "
         "move, or the last page change on the Page Organizer, Esc cancels a running job, and F1 opens this tab. While "
         "you are typing in a box, Ctrl+Z undoes your typing there; click away from the box (or use Ctrl+Shift+Z) "
         "to undo the tab's last change. On the Page Organizer, Ctrl+A selects every page and Delete removes the selected ones, and F5 looks for PDFs "
         "added, removed or changed in the folder."),
        ("Can I stop a job part-way?",
         "The PDF Splitter, Bulk File Mover and PDF Optimizer have a Cancel "
         "button (or press Esc). They stop after the file they are on, so "
         "nothing is ever left half done - what finished is finished, what "
         "was not started is listed in the summary. The PDF Merger handles "
         "one file and the Bulk Renamer always completes its run, because "
         "stopping a rename between its two steps would leave files under "
         "temporary names."),
        ("What is the 'Recent' button next to a folder?",
         "The last eight folders you used on that control since the app was "
         "opened, most recent first. Closing the app clears every Recent "
         "list. Folders that no longer exist drop off the list on their "
         "own. You can also drop a folder from Explorer straight onto the "
         "row - or drop any file in it, and its folder is used."),
        ("Where did my window and folders go when I reopened the app?",
         "Window size and position, the tab you were on and the Splitter's "
         "split point come back as you left them. Folders and files do not: "
         "closing the app clears every Recent list and every remembered "
         "folder (the Bulk Renamer's, the Mover's source and destination, "
         "the Page Organizer's, the Merger's reference PDF), so each session "
         "starts with no paths filled in."),
        ("How do I get to the files a tab just produced?",
         "Every tab has an Open button once it has finished: Open Folder on "
         "the Merger, Open Split Folder on the Splitter, Open Destination on "
         "the Mover, Open output folder on the Optimizer, Open Output Folder on the Page Organizer, Open result folder "
         "on the Renamer. Right-click any row in the Renamer or Optimizer "
         "tables for Open file, Open containing folder, Copy path and, in "
         "the Renamer, Exclude or Include. If you switched to another window "
         "while a batch ran, the taskbar entry flashes when it finishes."),
        ("Can I change the 'Split' folder name, the _Cover suffix, or how many "
         "undos are kept?",
         "Yes - Settings tab, the 'Advanced' section at the bottom. It holds "
         "the Splitter's folder name and both suffixes, the Optimizer's output "
         "folder name and its size-limit presets (the Preset button next to "
         "the MB box), the Page Organizer's output folder name, thumbnail size and "
         "'Replace the original' switch, how many overwritten-file backups the Merger keeps, how "
         "many rename or move runs can be undone in a row, which tab opens on "
         "launch, and which characters the Mover accepts after a file number. "
         "Everything defaults to how the program always worked, and a value "
         "that would not make a usable file name is refused rather than saved. "
         "The PDF OPTIMIZER and Dialogs rows above it apply the moment you "
         "tick them."),
    ]


    def __init__(self):
        super().__init__()

        outer, side = ink_shell(self, "About")
        outer.setSpacing(10)
        if setting("mascot_dialogs"):
            face = QLabel()
            face.setPixmap(mascot_pixmap("curious", 120))
            face.setAccessibleName("Pagey the paper mascot")
            face.setAlignment(Qt.AlignCenter)
            side.addWidget(face)
            who = QLabel("Who is Pagey?\nThe little paper character in the message boxes. Turn it off under Settings.")
            who.setObjectName("hint")
            who.setWordWrap(True)
            who.setAlignment(Qt.AlignCenter)
            side.addWidget(who)
            side.addSpacing(8)

        header = QLabel("Help")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        outer.addWidget(header)

        # Search: every word typed has to appear somewhere in a question or its
        # answer (any order, any case); the others are hidden.
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search Help, e.g. undo, Reorganized, recent")
        self.search_edit.setClearButtonEnabled(True)
        for _tb in self.search_edit.findChildren(QToolButton):     # Qt's built-in clear (x) button
            _tb.setToolTip("Clear the search")
            _tb.setAccessibleName("Clear the search")
        self.search_edit.setAccessibleName("Search Help")
        self.search_edit.setToolTip("Type a word or two to show only the questions that mention them (Esc clears)")
        self.search_edit.textChanged.connect(self._filter_faq)
        search_row.addWidget(self.search_edit, stretch=1)
        self.search_count = QLabel("")
        self.search_count.setObjectName("hint")
        search_row.addWidget(self.search_count)
        outer.addLayout(search_row)

        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 10, 0, 10)
        il.setSpacing(10)

        faq_title = QLabel("COMMON QUESTIONS")
        faq_title.setObjectName("sectionTitle")
        il.addWidget(faq_title)
        self._faq_rows = []                      # (question label, answer label, lower-cased text)
        for q, a in self.FAQ:
            al = _FaqAnswer(a)
            al.setObjectName("faqA")
            al.setWordWrap(True)
            nav = QWidget()
            nl = QHBoxLayout(nav)
            nl.setContentsMargins(0, 0, 0, 0)
            nl.setSpacing(8)
            for title in help_tabs_mentioned(q + " " + a):
                b = QPushButton(f"Go to {title}")
                b.setObjectName("secondary")
                b.setAccessibleName(f"Go to the {title} tab")
                b.setToolTip(f"Switch to the {title} tab")
                b.clicked.connect(_later(self._go_tab, title))
                nl.addWidget(b)
            nl.addStretch(1)
            nav.setVisible(False)
            al.nav = nav if nl.count() > 1 else None
            al.setVisible(False)                 # answers start closed; the question opens them
            ql = _FaqQuestion(q, al)
            il.addWidget(ql)
            il.addWidget(al)
            if al.nav is not None:
                il.addWidget(nav)
            self._faq_rows.append((ql, al, (q + " " + a).lower()))
        self.no_match = QLabel("Nothing in Help matches that. Try fewer or different words.")
        self.no_match.setObjectName("hint")
        self.no_match.setWordWrap(True)
        self.no_match.setVisible(False)
        il.addWidget(self.no_match)

        il.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll, stretch=1)

        self.update_label = QLabel("")
        self.update_label.setObjectName("status")
        self.update_label.setProperty("kind", "warn")
        self.update_label.setAlignment(Qt.AlignCenter)
        self.update_label.setWordWrap(True)
        self.update_label.setVisible(False)
        outer.addWidget(self.update_label)

        diag_row = side
        self.diag_btn = QPushButton("Copy diagnostics")
        self.diag_btn.setObjectName("secondary")
        self.diag_btn.setToolTip(
            "Copies version and settings information to the clipboard so you "
            "can paste it into an email. No file names or profile data.")
        self.diag_btn.clicked.connect(self._copy_diagnostics)
        diag_row.addWidget(self.diag_btn)
        open_logs = QPushButton("Open settings folder")
        open_logs.setObjectName("secondary")
        open_logs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(_log_dir())))
        diag_row.addWidget(open_logs)
        side.addStretch(1)

        about = QLabel(
            f"{APP_NAME} v{APP_VERSION}\n{APP_COPYRIGHT}\n{APP_NOTICE}"
        )
        about.setObjectName("legal")
        about.setAlignment(Qt.AlignCenter)
        about.setWordWrap(True)
        side.addWidget(about)

    def _go_tab(self, title: str) -> bool:
        """Switch the window to the tab a Help entry is about."""
        tabs = getattr(self.window(), "tabs", None)
        if tabs is None:
            return False
        for i in range(tabs.count()):
            label = tabs.tabBar().title(i) if hasattr(tabs.tabBar(), "title") else tabs.tabText(i)
            if label == title:
                tabs.setCurrentIndex(i)
                return True
        return False

    def _filter_faq(self, text: str = ""):
        words = text.lower().split()
        shown = 0
        for ql, al, hay in self._faq_rows:
            ok = all(w in hay for w in words)
            ql.setVisible(ok)
            al.setVisible(ok and (ql.expanded or bool(words)))     # a search shows the answers it matched
            shown += ok
        total = len(self._faq_rows)
        self.no_match.setVisible(bool(words) and shown == 0)
        self.search_count.setText(f"{shown} of {total}" if words else "")

    def _escape_pressed(self) -> bool:
        """Esc clears the search box first (MainWindow._escape asks before cancelling jobs)."""
        if self.search_edit.text():
            self.search_edit.clear()
            return True
        return False

    def showEvent(self, event):
        super().showEvent(event)
        # Checked when the Help tab is opened rather than at startup: reading a
        # network share must never be on the path between double-click and a
        # usable window.
        note = update_note()
        self.update_label.setText(note)
        self.update_label.setVisible(bool(note))

    def _copy_diagnostics(self):
        try:
            QApplication.clipboard().setText(diagnostics_text())
            self.diag_btn.setText("Copied - paste it into your email")
            QTimer.singleShot(4000,
                              lambda: self.diag_btn.setText("Copy diagnostics"))
        except Exception as e:
            Msg.warning(self, "Could not copy", str(e))




class _MovePreviewDialog(QDialog):
    """What WILL move, in full, before it moves. Accept = go."""

    def __init__(self, parent, src, dst, pairs, unmatched, folders_skipped):
        super().__init__(parent)
        self.setWindowTitle("Confirm Move")
        self.setModal(True)
        self.resize(720, 520)
        lay = QVBoxLayout(self)

        head = QLabel(f"Move {plural(len(pairs))}\n\nfrom: {src}\nto:   {dst}")
        head.setWordWrap(True)
        head_row = QHBoxLayout()
        head_row.setSpacing(14)
        if setting("mascot_dialogs"):
            face = QLabel()
            face.setPixmap(mascot_pixmap("curious", 72))
            face.setAccessibleName("Pagey the paper mascot")
            head_row.addWidget(face, 0, Qt.AlignVCenter)
        head_row.addWidget(head, stretch=1)
        lay.addLayout(head_row)

        table = QTableWidget(len(pairs), 2)
        table.setToolTip("The files that will be moved and the number each one matched")
        table.setHorizontalHeaderLabels(["File", "Matched number"])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.verticalHeader().setVisible(False)
        for r, (name, num) in enumerate(pairs):
            table.setItem(r, 0, QTableWidgetItem(name))
            table.setItem(r, 1, QTableWidgetItem(str(num)))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.setAccessibleName("Files that will move")
        lay.addWidget(table, stretch=1)
        self.table = table

        # Two labels, two colours: the unmatched numbers are a problem the
        # person should look at; a skipped folder is just information.
        if unmatched:
            note = QLabel(f"NO FILE MATCHED these {len(unmatched)} numbers:\n"
                          + ", ".join(unmatched))
            note.setObjectName("status")
            note.setProperty("kind", "error")
            note.setWordWrap(True)
            note.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(note)
        if folders_skipped:
            info = QLabel(f"{plural(folders_skipped, 'folder')} in the source "
                          "will be ignored - the mover only moves files.")
            info.setObjectName("status")
            info.setWordWrap(True)
            lay.addWidget(info)

        buttons = QDialogButtonBox()
        go = buttons.addButton(f"Move {len(pairs)} files", QDialogButtonBox.AcceptRole)
        go.setObjectName("primaryAction")
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        buttons.button(QDialogButtonBox.Cancel).setFocus()


class MoverPanel(_WorkerPanelMixin, QWidget):
    SHORTCUT_OPEN = "browse_source"
    SHORTCUT_RUN = "run_if_ready"
    SHORTCUT_UNDO = "undo_move"

    def __init__(self):
        super().__init__()
        self._busy = False
        self._worker = None
        self._run = None
        # A crash mid-move leaves last_move.jsonl.new behind; it is never read
        # (undo reads the published file), so it is litter - sweep it.
        try:
            os.remove(move_log_path() + ".new")
        except OSError:
            pass

        root, actions = ink_shell(self)

        header = QLabel("Bulk File Mover")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(header)

        root.addSpacing(6)
        step1 = QLabel("1. SELECT FOLDERS")
        step1.setObjectName("sectionTitle")
        root.addWidget(step1)

        self.source_row = FolderRow("Source Folder", self.browse_source,
                                    recent_key="mover_sources")
        root.addWidget(self.source_row)

        self.dest_row = FolderRow("Destination Folder", self.browse_dest,
                                  recent_key="mover_destinations")
        root.addWidget(self.dest_row)

        root.addSpacing(10)
        step2 = QLabel("2. ENTER FILE NUMBERS (ONE PER LINE)")
        step2.setObjectName("sectionTitle")
        root.addWidget(step2)

        self.text_input = QTextEdit()
        self.text_input.setAcceptRichText(False)     # Excel pastes its cell widths too: a narrow column wrapped each number
        self.text_input.setFixedHeight(220)
        root.addWidget(self.text_input)


        root.addStretch()

        btn_row = actions
        self.move_btn = QPushButton("Move Files Now")
        self.move_btn.setObjectName("primaryAction")
        self.move_btn.clicked.connect(self.execute_move)
        btn_row.addWidget(self.move_btn)
        self.source_row.changed.connect(self._sync_move_enabled)
        self.dest_row.changed.connect(self._sync_move_enabled)
        self.text_input.textChanged.connect(self._sync_move_enabled)

        # The Renamer has undo; the Mover had none, so a wrong destination
        # folder meant 500 files recoverable only by hand.
        self.undo_btn = QPushButton("Undo Last Move")
        self.undo_btn.setObjectName("secondary")
        self.undo_btn.clicked.connect(self.undo_move)
        self.undo_btn.setEnabled(os.path.exists(move_log_path()))
        btn_row.addWidget(self.undo_btn)
        self.open_btn = QPushButton("Open Destination")
        self.open_btn.setObjectName("secondary")
        self.open_btn.clicked.connect(self._open_dest)
        btn_row.addWidget(self.open_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("secondary")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self.cancel_btn)
        actions.addStretch(1)
        self._sync_move_enabled()

        self.status = QLabel("Ready")
        self.status.setObjectName("status")
        root.addWidget(self.status)

        state = load_ui_state()
        for row, key in ((self.source_row, "mover_last_source"),
                         (self.dest_row, "mover_last_dest")):
            last = state.get(key, "")
            # No isdir() at construction - see MergerPanel. execute_move
            # already explains an unreadable source folder.
            if isinstance(last, str) and last:
                row.set_path(last, remember=False)
        self.source_row.changed.connect(
            lambda p: p and update_ui_state(mover_last_source=p))
        self.dest_row.changed.connect(
            lambda p: p and update_ui_state(mover_last_dest=p))

    def _open_dest(self):
        if not open_in_explorer(self.dest_row.path()):
            self._set_status("Choose a destination folder first.", "error")

    def _set_status(self, text: str, kind: str = ""):
        self.status.setText(text)
        self.status.setProperty("kind", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def undo_move(self):
        if self._busy:
            return
        self._set_busy(True)
        self._set_status("Undoing...", "busy")
        self._worker = _BatchWorker([None], lambda _x: undo(move_log_path()), self)
        self._worker.item_done.connect(self._on_undo_done)
        self._worker.item_failed.connect(self._on_undo_failed)
        self._worker.finished_all.connect(self._on_undo_finished)
        self._worker.start()

    def _on_undo_done(self, _i, _x, result):
        restored, failures = result
        if restored == 0 and not failures:
            self._notify("warning", "Nothing To Undo",
                         "The last move left no record that can be reversed.")
        elif failures:
            self._notify("warning", "Undo Completed With Errors",
                         f"Put back: {restored}\n\n" + "\n".join(failures[:20]))
        else:
            self._notify("information", "Undo Complete",
                         f"Put back {plural(restored)}.")
        self._set_status(f"Undo: {plural(restored)} put back.",
                         "error" if failures else "ok")

    def _on_undo_failed(self, _i, _x, message):
        # undo() raises RenameExecError when there is nothing to undo.
        self._notify("information", "Undo", message)

    def _on_undo_finished(self, _cancelled):
        self._worker = None
        self._set_busy(False)

    def browse_source(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Source Folder", self.source_row.path() or "")
        if folder:
            self.source_row.set_path(folder)

    def browse_dest(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Destination Folder", self.dest_row.path() or "")
        if folder:
            self.dest_row.set_path(folder)

    def receive_folder(self, folder: str) -> bool:
        """From the tray: this becomes the source folder."""
        if not os.path.isdir(folder):
            self._set_status("That folder is not there any more.", "error")
            return False
        self.source_row.set_path(folder)
        return True

    def run_if_ready(self):
        """Ctrl+Enter: the same as pressing the button, which is greyed until the inputs are filled."""
        if self.move_btn.isEnabled():
            self.execute_move()

    def execute_move(self):
        if self._busy:
            return
        src = self.source_row.path()
        dst = self.dest_row.path()
        raw_input = self.text_input.toPlainText().splitlines()
        file_numbers = list(dict.fromkeys(line.strip() for line in raw_input if line.strip()))

        if not src or not dst or not file_numbers:
            Msg.warning(
                self, "Missing Info",
                "Please select both folders and enter at least one file number."
            )
            return

        src_abs = os.path.normcase(os.path.abspath(src))
        dst_abs = os.path.normcase(os.path.abspath(dst))
        if src_abs == dst_abs:
            Msg.critical(self, "Invalid Folders", "Source and destination cannot be the same folder.")
            return
        # A destination nested inside the source could be matched and moved
        # into itself.
        if dst_abs.startswith(src_abs + os.sep) or src_abs.startswith(dst_abs + os.sep):
            Msg.critical(
                self, "Invalid Folders",
                "One of these folders is inside the other. Choose folders "
                "that sit side by side.")
            return

        try:
            entries = os.listdir(src)
        except OSError as e:
            Msg.critical(self, "Error", f"Could not read source folder: {e}")
            return

        # FILES only. Without this, a case SUBFOLDER whose name starts with a
        # typed number was moved wholesale by shutil.move.
        files_in_source = [n for n in entries
                           if os.path.isfile(os.path.join(src, n))]
        folders_skipped = len(entries) - len(files_in_source)

        pairs, unmatched = match_by_number(files_in_source, file_numbers)
        matched = [name for name, _num in pairs]

        if not matched:
            Msg.information(
                self, "No Matches",
                "No files in the source folder matched the given numbers.\n\n"
                "Numbers with no match: " + ", ".join(unmatched[:20]))
            return

        # A real preview: every match in a table, every unmatched number in
        # full. The old QMessageBox cut the list at 12 and the numbers at 20,
        # which is exactly where a 300-file move needs the most looking at.
        if not _MovePreviewDialog(self, src, dst, pairs, unmatched,
                                  folders_skipped).exec():
            return

        self._start_move(src, dst, matched, unmatched, folders_skipped)

    # ------------------------------------------------------------ the run
    def _start_move(self, src, dst, matched, unmatched, folders_skipped):
        """Off the UI thread, one file per item; Cancel stops between files.

        The worker only moves. The undo log is written HERE, on the UI
        thread, from item_done - one writer, and a cancelled run still
        leaves an accurate record of exactly the files that did move."""
        self._run = {
            "src": src, "dst": dst, "matched": matched, "unmatched": unmatched,
            "folders_skipped": folders_skipped,
            "moved": [], "skipped": [], "failed": [], "too_long": [],
            "log": None,
        }
        try:
            self._run["log"] = open(move_log_path() + ".new", "w", encoding="utf-8")
        except OSError as e:
            Msg.critical(self, "Error",
                                 f"Could not open the move log, so nothing was moved: {e}")
            self._run = None
            return
        self._set_busy(True)
        self._set_status(f"Moving {plural(len(matched))}...", "busy")

        def move_one(file_name):
            dest_path = os.path.join(dst, file_name)
            if len(dest_path) >= max_path_len():
                return ("too_long", None, None)
            if os.path.exists(dest_path):
                return ("skipped", None, None)
            source_path = os.path.join(src, file_name)
            shutil.move(source_path, dest_path)
            return ("moved", source_path, dest_path)

        self._worker = _BatchWorker(
            matched, move_one, self,
            path_of=lambda name: os.path.join(src, name))
        self._worker.item_done.connect(self._on_item_done)
        self._worker.item_failed.connect(self._on_item_failed)
        self._worker.finished_all.connect(self._on_finished)
        self._worker.start()

    def _move_ready(self) -> bool:
        return bool(self.source_row.path() and self.dest_row.path()
                    and self.text_input.toPlainText().strip())

    def _sync_move_enabled(self, *_a):
        ready = self._move_ready()
        self.move_btn.setEnabled(ready and not self._busy)
        self.move_btn.setToolTip("Move the matching files now" if ready else
                                 "Choose both folders and enter at least one file number first")

    def _set_busy(self, busy: bool):
        self._busy = busy
        self._sync_move_enabled()
        self.cancel_btn.setEnabled(busy)
        self.text_input.setReadOnly(busy)
        self.source_row.setEnabled(not busy)
        self.dest_row.setEnabled(not busy)
        self.open_btn.setEnabled(not busy)
        self.undo_btn.setEnabled(
            (not busy) and os.path.exists(move_log_path()))

    def _on_item_done(self, _i, file_name, result):
        run = self._run
        outcome, source_path, dest_path = result
        if outcome == "moved":
            # The log line goes down BEFORE the file counts as moved, and the
            # buffer is flushed so a crash between files cannot leave a moved
            # file with no undo record.
            try:
                run["log"].write(json.dumps({"step": "final", "dst": dest_path,
                                             "orig": source_path}) + "\n")
                run["log"].flush()
            except OSError as e:
                run["failed"].append(
                    f"{file_name} was moved but could not be recorded for undo "
                    f"({e.strerror or e}). Put it back by hand if needed.")
                return
            run["moved"].append(file_name)
            self._set_status(
                f"Moving... {len(run['moved'])} of {len(run['matched'])}", "busy")
        else:
            run[outcome].append(file_name)

    def _on_item_failed(self, _i, _file_name, message):
        self._run["failed"].append(message)

    def _on_finished(self, cancelled: bool):
        run, self._run = self._run, None
        self._worker = None
        self._alert_done()
        try:
            run["log"].close()
            if run["moved"]:
                archive_undo_log(move_log_path())
                os.replace(move_log_path() + ".new", move_log_path())
            else:
                os.remove(move_log_path() + ".new")
        except OSError:
            pass
        self._set_busy(False)

        moved, skipped, failed, too_long = (
            run["moved"], run["skipped"], run["failed"], run["too_long"])
        unmatched, folders_skipped = run["unmatched"], run["folders_skipped"]
        not_started = (len(run["matched"]) - len(moved) - len(skipped)
                       - len(failed) - len(too_long))
        summary = [f"Moved: {len(moved)}"]
        if cancelled:
            summary.append(f"Cancelled: {not_started} not started")
        if skipped:
            summary.append(f"Skipped (already at the destination): {len(skipped)}")
        if too_long:
            summary.append(f"Skipped (path too long for Windows): {len(too_long)}")
        if folders_skipped:
            summary.append(f"Ignored (folders, not files): {folders_skipped}")
        if unmatched:
            summary.append("No file matched: " + ", ".join(unmatched[:20]))
        if failed:
            summary.append(f"Failed: {len(failed)}")
        msg = "\n".join(summary)
        self._set_status(
            f"Moved {plural(len(moved))}." + (" Undo is available." if moved else ""),
            "error" if (failed or unmatched) else "ok")
        if failed or unmatched or too_long:
            self._notify("warning", "Completed With Notes",
                         msg + ("\n\n" + "\n".join(failed) if failed else ""))
        elif cancelled:
            self._notify("information", "Cancelled", msg)
        else:
            self._notify("information", "Success", msg)


class _OptimizeWorker(QThread):
    """
    One file at a time, off the UI thread.

    Deliberately breaks this file's usual processEvents() pattern: a large
    scan takes tens of seconds and a batch takes minutes, which
    processEvents() cannot rescue because optimize_pdf() is one long call.

    The engine calls back into this thread per page, which is what makes the
    progress bar move and Cancel respond inside a file rather than only
    between files.
    """

    file_started = Signal(str)          # path
    file_done = Signal(str, object)     # path, OptimizeResult
    file_failed = Signal(str, str)      # path, plain-language message
    file_cancelled = Signal(str)        # path
    stage = Signal(str, str, int, int)  # path, stage text, done, total
    file_progress = Signal(int, int)    # files done, files total
    finished_all = Signal()

    def __init__(self, jobs, options, parent=None):
        super().__init__(parent)
        self._jobs = list(jobs)             # list[str] of source paths
        self._options = options
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def is_cancelled(self):
        return self._cancel

    def run(self):
        total = len(self._jobs)
        for i, path in enumerate(self._jobs, start=1):
            if self._cancel:
                self.file_cancelled.emit(path)
                continue
            self.file_started.emit(path)
            try:
                result = optimize_pdf(
                    path, self._options,
                    on_progress=lambda s, d, t, p=path: self.stage.emit(p, s, d, t),
                    should_cancel=self.is_cancelled,
                )
                if self._cancel and not result.ok:
                    self.file_cancelled.emit(path)
                elif result.ok:
                    self.file_done.emit(path, result)
                else:
                    self.file_failed.emit(path, result.error or "Skipped.")
            except PdfOpError as exc:
                self.file_failed.emit(path, str(exc))
            except OSError as exc:
                # The app already knows how to explain a locked file; the
                # Optimizer used to report "Unexpected problem: PermissionError."
                self.file_failed.emit(path, describe_lock_failure(path, exc))
            except Exception as exc:
                self.file_failed.emit(
                    path,
                    f"Could not finish this file ({type(exc).__name__}). "
                    "Please report which file it was."
                )
            self.file_progress.emit(i, total)
        self.finished_all.emit()


class _InspectWorker(QThread):
    """Report-only run: reads each file, writes nothing."""

    file_started = Signal(str)
    file_done = Signal(str, object)     # path, inspect_pdf() dict
    file_failed = Signal(str, str)
    file_progress = Signal(int, int)
    finished_all = Signal()

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self._jobs = list(jobs)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        total = len(self._jobs)
        for i, path in enumerate(self._jobs, start=1):
            if self._cancel:
                break
            self.file_started.emit(path)
            if not os.path.isfile(path):
                self.file_failed.emit(path, "That file is no longer where it was.")
                self.file_progress.emit(i, total)
                continue
            try:
                self.file_done.emit(path, inspect_pdf(path))
            except PdfOpError as exc:
                self.file_failed.emit(path, str(exc))
            except OSError as exc:
                self.file_failed.emit(path, describe_lock_failure(path, exc))
            except Exception as exc:
                self.file_failed.emit(
                    path, f"Could not read this file ({type(exc).__name__}).")
            self.file_progress.emit(i, total)
        self.finished_all.emit()


class OptimizerPanel(QWidget):
    """
    Shrink a PDF under a size limit and strip hidden information.

    Rows are keyed by SOURCE PATH, not by row index, because the results
    table is sortable - a row index recorded when a job was queued does not
    survive the operator clicking a column header.
    """
    _shutting_down = False
    SHORTCUT_OPEN = "_browse_files"
    SHORTCUT_RUN = "_run_again"
    SHORTCUT_UNDO = None

    COLUMNS = ["File", "Before", "After", "What it had to do", "Status"]
    COL_FILE, COL_BEFORE, COL_AFTER, COL_ACTION, COL_STATUS = range(5)

    def __init__(self):
        super().__init__()

        self._busy = False
        self._worker = None
        self._results = {}          # path -> OptimizeResult
        self._reports = {}          # path -> inspect_pdf() dict (report-only runs)
        self._report_run = False
        self._failed = {}           # path -> message
        self._last_paths = []       # for "Run again"
        self._ignored = []
        self._started_at = None

        root, actions = ink_shell(self)
        root.setSpacing(10)

        header = QLabel("PDF Optimizer")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(header)

        # Two columns rather than one tall stack. Measured before the change:
        # this tab's sizeHint was 563x864 while no other tab needed more than
        # 563 in height, so the Optimizer alone was setting how tall the whole
        # window had to be.
        body = QHBoxLayout()
        body.setSpacing(16)
        root.addLayout(body, stretch=1)

        # ---- left column: what you set, and where you drop -------------
        left = QVBoxLayout()
        left.setSpacing(8)
        left.setContentsMargins(0, 1, 0, 0)       # a widget's layout defaults to a 9 px margin; the tab margin is already 24
        left_host = QWidget()
        left_host.setLayout(left)
        left_host.setFixedWidth(CONTROL_PANE_W)
        body.addWidget(left_host)

        step1 = QLabel("1. SET THE LIMIT")
        step1.setObjectName("sectionTitle")
        left.addWidget(step1)

        opts_panel = QFrame()
        opts_panel.setObjectName("panel")
        opts_layout = QVBoxLayout(opts_panel)
        opts_layout.setContentsMargins(14, 10, 14, 10)
        opts_layout.setSpacing(6)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Maximum file size:"))
        self.ceiling_box = QLineEdit(f"{self._saved_ceiling():g}")
        self.ceiling_box.setFixedWidth(72)
        self.ceiling_box.setAccessibleName("Size limit in megabytes")
        self.ceiling_box.editingFinished.connect(self._remember_ceiling)
        size_row.addWidget(self.ceiling_box)
        size_row.addWidget(QLabel("MB"))
        self.preset_btn = QPushButton("Preset \u25be")
        self.preset_btn.setObjectName("secondary")
        self.preset_btn.setAccessibleName("Size limit presets")
        self.preset_btn.clicked.connect(self._show_presets)
        size_row.addWidget(self.preset_btn)
        size_row.addStretch(1)
        opts_layout.addLayout(size_row)

        # The one deliberate exception to the no-checkboxes policy below. It
        # is not a quality knob - it selects a different JOB. Everything else
        # answers "make this fit"; this answers "make this flat", which the
        # size ladder cannot express because it only flattens as a last
        # resort and only when the file is over the limit.
        self.force_flatten_box = QCheckBox(
            "Flatten pages to images (ignore the size limit)")
        self.force_flatten_box.setAccessibleName(
            "Flatten pages to images, ignoring the maximum file size")
        self.force_flatten_box.setToolTip(
            "Re-renders every page as a picture, whatever the result weighs.\n"
            "The maximum file size above is not applied.\n"
            "The copy is no longer searchable and has no annotations, form "
            "fields or bookmarks. Your original is untouched.")
        self.force_flatten_box.toggled.connect(self._on_force_flatten_toggled)
        opts_layout.addWidget(self.force_flatten_box)

        # Like the flatten box, this selects a different JOB, not a quality
        # level: it answers "what is in this file and what would you remove"
        # without writing anything.
        self.report_only_box = QCheckBox("Report only (inspect, change nothing)")
        self.report_only_box.setAccessibleName(
            "Report only: inspect the files and write nothing")
        self.report_only_box.setToolTip(
            "Reads each PDF and tells you what is taking up the space and what\n"
            "the Optimizer would remove. No copy is made and nothing is changed.")
        self.report_only_box.toggled.connect(self._on_report_only_toggled)
        opts_layout.addWidget(self.report_only_box)

        how = QLabel(
            "It does the least damaging thing that works:\n"
            "1.  Removes hidden information. If that is enough it stops "
            "there — the file stays intact and searchable.\n"
            "2.  Shrinks the scans — colour first, then resolution.\n"
            "3.  Only if still too big, turns the pages into flat pictures. "
            "Always fits, but no longer searchable and no annotations.\n"
            "'What it had to do' tells you which of the three it needed."
        )
        how.setObjectName("hint")
        how.setWordWrap(True)
        opts_layout.addWidget(how)
        left.addWidget(opts_panel)

        step2 = QLabel("2. ADD YOUR PDFs")
        step2.setObjectName("sectionTitle")
        left.addWidget(step2)

        self._drop_text = ("DROP PDF FILES OR A FOLDER HERE\n"
                           "originals are kept — each file's optimized copy goes\n"
                           f"into an '{setting('optimized_dir_name')}' folder in its own folder")
        self.drop_zone = DropZone(self.handle_drop, text=self._drop_text)
        # Bounded, then a spacer takes the rest. Unbounded it grew to ~490px
        # on a tall window and read as an empty panel - the same thing
        # DROPZONE_STRETCH / SPACER_STRETCH were introduced to stop elsewhere.
        self.drop_zone.setMaximumHeight(260)
        left.addWidget(self.drop_zone, stretch=DROPZONE_STRETCH)

        # Drag-and-drop into a Remote Desktop session frequently does not
        # fire, and every other tab offers a button. Without this the tab is
        # unusable over RDP.
        pick_row = actions
        self.browse_button = QPushButton("Choose PDFs...")
        self.browse_button.setObjectName("primaryAction")
        self.browse_button.clicked.connect(self._browse_files)
        pick_row.addWidget(self.browse_button)
        self.browse_folder_button = QPushButton("Choose Folder...")
        self.browse_folder_button.setObjectName("secondary")
        self.browse_folder_button.clicked.connect(self._browse_folder)
        pick_row.addWidget(self.browse_folder_button)

        prog_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        prog_row.addWidget(self.progress, stretch=1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("secondary")
        self.cancel_button.setVisible(False)
        self.cancel_button.setFixedWidth(96)
        self.cancel_button.clicked.connect(self._cancel)
        prog_row.addWidget(self.cancel_button)
        left.addLayout(prog_row)

        policy = QLabel(
            "Always removed: document properties, hidden metadata, embedded "
            "files, JavaScript, automatic actions, page thumbnails, and every "
            "earlier saved version. Your original is never changed, and an "
            "existing optimized copy is never replaced."
        )
        policy.setObjectName("hint")
        policy.setWordWrap(True)
        left.addWidget(policy)

        left.addStretch(SPACER_STRETCH)

        self.status = QLabel("Ready")
        self.status.setObjectName("status")
        left.addWidget(self.status)

        # ---- right column: what came out ------------------------------
        right = QVBoxLayout()
        right.setSpacing(8)
        right.setContentsMargins(0, 1, 0, 0)
        right_host = QWidget()
        right_host.setLayout(right)
        body.addWidget(right_host, stretch=1)

        results_row = QHBoxLayout()
        step3 = QLabel("3. RESULTS")
        step3.setObjectName("sectionTitle")
        results_row.addWidget(step3)
        results_row.addStretch(1)
        self.again_button = QPushButton("Run again")
        self.again_button.setObjectName("secondary")
        self.again_button.setEnabled(False)
        self.again_button.setToolTip(
            "Run the same files again — useful after changing the size limit."
        )
        self.again_button.clicked.connect(self._run_again)
        pick_row.addWidget(self.again_button)             # next to the buttons that choose the files
        self.open_button = QPushButton("Open output folder")
        self.open_button.setObjectName("secondary")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_output_folder)
        actions.addWidget(self.open_button)
        self.save_button = QPushButton("Save results...")
        self.save_button.setObjectName("secondary")
        self.save_button.setEnabled(False)
        self.save_button.setToolTip(
            "Write what was done to each file to a .csv you can keep or send on."
        )
        self.save_button.clicked.connect(self._save_results)
        actions.addWidget(self.save_button)
        right.addLayout(results_row)
        actions.addStretch(1)
        # "1. SET THE LIMIT" and "3. RESULTS" must sit on one baseline: the
        # results row is as tall as its buttons, so the left title gets the
        # same height and centres in it.
        step1.setMinimumHeight(self.open_button.sizeHint().height())
        step1.setAlignment(Qt.AlignVCenter)

        # A splitter, so the operator decides how much room the detail pane
        # gets rather than the layout deciding for them. Same device the
        # Bulk Renamer already uses.
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(120)
        self.table.setSortingEnabled(True)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(self.COL_FILE, QHeaderView.Stretch)
        for col in range(1, len(self.COLUMNS)):
            head.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.cellClicked.connect(self._show_detail)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.cellDoubleClicked.connect(self._open_row)
        split.addWidget(self.table)

        detail_panel = QFrame()
        detail_panel.setObjectName("panel")
        dpl = QVBoxLayout(detail_panel)
        dpl.setContentsMargins(14, 10, 14, 10)
        self.detail_label = QLabel(
            "Click a row to see what is in that file and exactly what was "
            "removed from it. Double-click a row to open the optimized copy."
        )
        self.detail_label.setObjectName("peek")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        dpl.addWidget(self.detail_label)

        # The detail text is long on a file that needed explaining, so it
        # scrolls instead of forcing the whole tab taller.
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setWidget(detail_panel)
        detail_scroll.setFrameShape(QFrame.NoFrame)
        detail_scroll.setMinimumHeight(110)
        split.addWidget(detail_scroll)

        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        # Explicit initial sizes: stretch factors alone left the detail pane
        # at ~23% because its sizeHint is small, and the detail pane is the
        # thing that explains a file that did not fit.
        split.setSizes([320, 220])
        right.addWidget(split, stretch=1)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _mb(n_bytes: int) -> str:
        return f"{n_bytes / 1_000_000:.2f} MB"

    @staticmethod
    def _saved_ceiling() -> float:
        """The size limit does not change between launches; retyping it
        every morning is a papercut the app already has the machinery to fix."""
        try:
            value = float(load_ui_state().get("optimizer_ceiling_mb",
                                              DEFAULT_SIZE_CEILING_MB))
            if 0 < value <= 2000:
                return value
        except (TypeError, ValueError):
            pass
        return DEFAULT_SIZE_CEILING_MB

    def _on_force_flatten_toggled(self, on):
        """The size limit is not applied on a forced run, so it is disabled
        rather than left looking live - a control that is read on some runs
        and silently ignored on others is how an operator learns to distrust
        the panel."""
        self._sync_limit_controls()

    def _sync_limit_controls(self):
        busy = bool(self._busy)
        flat = self.force_flatten_box.isChecked()
        rep_only = self.report_only_box.isChecked()
        self.ceiling_box.setEnabled(not busy and not flat and not rep_only)
        self.preset_btn.setEnabled(not busy and not rep_only)
        self.force_flatten_box.setEnabled(not busy and not rep_only)

    def _on_report_only_toggled(self, on):
        """A report applies no limit and makes no copy, so the controls that
        belong to the other job are greyed rather than left looking live."""
        if on:
            self.force_flatten_box.setChecked(False)
        self._sync_limit_controls()
        self.drop_zone.setText(
            "DROP PDF FILES OR A FOLDER HERE\n"
            "report only — nothing is written or changed" if on else
            self._drop_text)

    def _show_presets(self):
        menu = QMenu(self)
        for name, mb in setting("ceiling_presets"):
            menu.addAction(f"{name}  \u2014  {mb:g} MB",
                           _later(self._apply_preset, mb))
        menu.addSeparator()
        menu.addAction("Edit presets on the Settings tab...").setEnabled(False)
        menu.exec(self.preset_btn.mapToGlobal(self.preset_btn.rect().bottomLeft()))
        menu.deleteLater()

    def _apply_preset(self, mb: float):
        self.ceiling_box.setText(f"{mb:g}")
        self._remember_ceiling()

    def _remember_ceiling(self):
        mb = self.ceiling_bytes()
        if mb is None:
            return
        try:
            state = load_ui_state()
            state["optimizer_ceiling_mb"] = mb / 1_000_000
            save_ui_state(state)
        except Exception:
            pass                                   # never block work on this

    def _set_status(self, text: str, kind: str = ""):
        self.status.setText(text)
        self.status.setProperty("kind", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def ceiling_bytes(self):
        """Returns bytes, or None if the box does not hold a usable number."""
        try:
            mb = float(self.ceiling_box.text().strip())
        except ValueError:
            return None
        if mb <= 0 or mb > 2000:
            return None
        return int(mb * 1_000_000)

    def _row_for(self, path):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_FILE)
            if item is not None and item.data(Qt.UserRole) == path:
                return row
        return -1

    def _path_at(self, row):
        item = self.table.item(row, self.COL_FILE)
        return item.data(Qt.UserRole) if item is not None else None

    def _set_busy(self, busy: bool):
        self._busy = busy
        self.drop_zone.setAcceptDrops(not busy)
        self._sync_limit_controls()
        self.browse_button.setEnabled(not busy)
        self.browse_folder_button.setEnabled(not busy)
        self.again_button.setEnabled(not busy and bool(self._last_paths))
        self.open_button.setEnabled(not busy and bool(self._results))
        self.save_button.setEnabled(not busy and bool(
            self._results or self._failed or self._reports))
        self.report_only_box.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        # A row index captured while queueing does not survive a sort, so
        # sorting is off for the duration of a run rather than corrupting it.
        self.table.setSortingEnabled(not busy)

    def is_busy(self):
        return self._busy

    def stop_for_shutdown(self, wait_ms=15000):
        """Called by MainWindow.closeEvent. Returns True once the thread is
        actually finished - closing the app on a live QThread is a crash."""
        self._shutting_down = True
        if self._worker is None:
            return True
        self._worker.cancel()
        return self._worker.wait(wait_ms)

    # -------------------------------------------------------------- inputs
    def _browse_files(self):
        if self._busy:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose PDFs to optimize", "", "PDF files (*.pdf)")
        if paths:
            self.handle_drop(paths)

    def _browse_folder(self):
        if self._busy:
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of PDFs")
        if folder:
            self.handle_drop([folder])

    @staticmethod
    def _expand(paths):
        """Files and folders in, PDFs out. Dropping a folder used to be
        rejected with 'Please drop one or more PDF files', which is exactly
        what the operator thought they had done."""
        pdfs, ignored = [], []
        for path in paths:
            if os.path.isdir(path):
                found = collect_files(path, recurse=False, ext_filter=".pdf")
                if found:
                    pdfs.extend(found)
                else:
                    ignored.append(os.path.basename(path) + " (no PDFs in it)")
            elif path.lower().endswith(".pdf") and os.path.isfile(path):
                pdfs.append(path)
            else:
                ignored.append(os.path.basename(path))
        seen, unique = set(), []
        for p in pdfs:
            key = os.path.normcase(os.path.abspath(p))
            if key not in seen:
                seen.add(key)
                unique.append(p)
        return unique, ignored

    # -------------------------------------------------------------- actions
    def receive_paths(self, paths):
        """Another tab sent files: same path as dropping them here."""
        if not classify_drop(paths)["pdfs"]:
            return False
        self.handle_drop(list(paths))
        return True

    def handle_drop(self, paths):
        if self._busy:                                   # re-entrancy guard
            return
        pdfs, ignored = self._expand(paths)
        if not pdfs:
            Msg.critical(
                self, "Nothing To Optimize",
                "No PDF files were found in what you added.\n\n"
                + ("Skipped: " + ", ".join(ignored[:8]) if ignored else
                   "Add PDF files, or a folder that contains them."))
            return
        self._start(pdfs, ignored)

    def _run_again(self):
        if self._busy or not self._last_paths:
            return
        still_there = [p for p in self._last_paths if os.path.isfile(p)]
        if not still_there:
            Msg.warning(self, "Run Again",
                                "Those files are no longer where they were.")
            return
        self._start(still_there, [])

    def _start_report(self, pdfs, ignored):
        self._results.clear()
        self._reports.clear()
        self._failed.clear()
        self._ignored = ignored
        self._last_paths = list(pdfs)
        self._report_run = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for path in pdfs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem(os.path.basename(path))
            name_item.setData(Qt.UserRole, path)
            name_item.setToolTip(path)
            self.table.setItem(row, self.COL_FILE, name_item)
            try:
                before = self._mb(os.path.getsize(path))
            except OSError:
                before = "?"
            self.table.setItem(row, self.COL_BEFORE, QTableWidgetItem(before))
            self.table.setItem(row, self.COL_AFTER, QTableWidgetItem("—"))
            self.table.setItem(row, self.COL_ACTION, QTableWidgetItem("Report only"))
            self.table.setItem(row, self.COL_STATUS, QTableWidgetItem("Waiting"))
            self._paint_row(row, "waiting")
        self._files_total = len(pdfs)
        self._files_done = 0
        self._started_at = time.monotonic()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Reading...")
        self._set_busy(True)
        self.detail_label.setText("Reading...")
        self._set_status(f"Inspecting {plural(len(pdfs))}...", "busy")
        self._worker = _InspectWorker(pdfs, self)
        self._worker.file_started.connect(self._on_started)
        self._worker.file_done.connect(self._on_report_done)
        self._worker.file_failed.connect(self._on_failed)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.finished_all.connect(self._on_report_finished)
        self._worker.start()

    def _on_report_done(self, path, rep):
        self._reports[path] = rep
        row = self._row_for(path)
        if row < 0:
            return
        n = sum(rep["would_remove"].values()) if rep["would_remove"] else 0
        status = (f"Inspected — {plural(len(rep['would_remove']), 'kind')} of hidden "
                  "information found" if rep["would_remove"]
                  else "Inspected — no hidden information found")
        if rep["signed"]:
            status += "  ·  SIGNED — optimizing would void it"
        self.table.item(row, self.COL_STATUS).setText(status)
        self._paint_row(row, "warn" if rep["signed"] else "ok")

    def _on_report_finished(self):
        try:
            done = len(self._reports)
            failed = len(self._failed)
            stopped = self.table.rowCount() - done - failed
            for row in range(self.table.rowCount()):
                src = self._path_at(row)
                if src not in self._reports and src not in self._failed:
                    self.table.item(row, self.COL_STATUS).setText("Not done — you stopped the run")
                    self._paint_row(row, "muted")
            bits = [f"Inspected: {done}"]
            if failed:
                bits.append(f"Could not be read: {failed}")
            if stopped > 0:
                bits.append(f"Not done (you stopped): {stopped}")
            if self._ignored:
                bits.append("Skipped (not a PDF): " + ", ".join(self._ignored[:6]))
            self.detail_label.setText(
                "   |   ".join(bits)
                + "\nClick a row for its report. Nothing was written or changed.")
            self._set_status("Report ready." if not (failed or stopped > 0)
                             else "Report finished with items needing attention.",
                             "ok" if not (failed or stopped > 0) else "error")
            if not self._shutting_down:
                self._alert_if_away()
        finally:
            self._set_busy(False)
            self.progress.setFormat("")
            self._worker = None

    def _alert_if_away(self):
        try:
            win = self.window()
            if win is not None and not win.isActiveWindow():
                QApplication.alert(win, 0)
        except Exception:
            pass

    def _start(self, pdfs, ignored):
        if self.report_only_box.isChecked():
            self._start_report(pdfs, ignored)
            return
        self._report_run = False
        self._reports.clear()
        forced = self.force_flatten_box.isChecked()
        ceiling = self.ceiling_bytes()
        if forced and ceiling is None:
            ceiling = int(DEFAULT_SIZE_CEILING_MB * 1_000_000)  # unused, forced
        if ceiling is None:
            Msg.warning(
                self, "Maximum File Size",
                "Enter the maximum file size in MB, for example 6.9.\n\n"
                "It has to be a number between 0 and 2000. If you meant "
                "kilobytes, 7000 KB is 7 MB.")
            self.ceiling_box.setFocus()
            self.ceiling_box.selectAll()
            return
        self._remember_ceiling()

        if forced:
            confirm = Msg.question(
                self, "Confirm Flatten",
                f"Flatten {plural(len(pdfs))} to {DEFAULT_FLATTEN_DPI} DPI "
                f"images?\n\n"
                f"Every page becomes a picture. The maximum file size is NOT "
                f"applied — the result may be larger than the limit, or even "
                f"larger than the original.\n\n"
                f"The copies are NOT searchable and have no annotations, form "
                f"fields or bookmarks. Your originals are left untouched, in "
                f"an '{OPTIMIZED_DIR_NAME}' folder inside each file's own "
                f"folder.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
            confirm = QMessageBox.Yes
        else:
            confirm = Msg.question(
            self, "Confirm Optimize",
            f"Optimize {plural(len(pdfs))} to at most "
            f"{ceiling / 1_000_000:.1f} MB each?\n\n"
            f"Your originals are left untouched. Each optimized copy is saved "
            f"into an '{OPTIMIZED_DIR_NAME}' folder inside that file's own "
            f"folder, and an existing copy is never replaced.\n\n"
            f"If removing hidden information and shrinking the scans is not "
            f"enough, the pages are turned into flat pictures to make the "
            f"size. Those files are NOT searchable and have no annotations — "
            f"keep your originals.\n\n"
            f"Large scans take a while. You can stop the run at any time.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        # Fixed policy, deliberately not exposed as controls. The
        # permutations were run against real files and this is the combination that
        # works; every extra checkbox was a way to get it wrong. The engine
        # still accepts the other options if one is ever needed again.
        options = OptimizeOptions(
            mode="smallest",
            out_dir_name=setting("optimized_dir_name"),
            ceiling_bytes=ceiling,
            allow_gray=True,
            allow_flatten=bool(setting("optimizer_allow_flatten")),
            flatten_dpi=int(setting("optimizer_flatten_dpi")),
            force_flatten=forced,
            sanitize=SanitizeOptions(),
        )

        self._results.clear()
        self._failed.clear()
        self._ignored = ignored
        self._last_paths = list(pdfs)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.detail_label.setText("Working...")

        for path in pdfs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem(os.path.basename(path))
            name_item.setData(Qt.UserRole, path)
            name_item.setToolTip(path)          # two case folders, same filename
            self.table.setItem(row, self.COL_FILE, name_item)
            try:
                self.table.setItem(row, self.COL_BEFORE,
                                   QTableWidgetItem(self._mb(os.path.getsize(path))))
            except OSError:
                self.table.setItem(row, self.COL_BEFORE, QTableWidgetItem("?"))
            self.table.setItem(row, self.COL_AFTER, QTableWidgetItem("—"))
            self.table.setItem(row, self.COL_ACTION, QTableWidgetItem("—"))
            self.table.setItem(row, self.COL_STATUS, QTableWidgetItem("Waiting"))
            self._paint_row(row, "waiting")

        self._files_total = len(pdfs)
        self._files_done = 0
        self._started_at = time.monotonic()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Starting...")
        self._set_busy(True)
        self._set_status(f"Optimizing {plural(len(pdfs))}...", "busy")

        self._worker = _OptimizeWorker(pdfs, options, self)
        self._worker.file_started.connect(self._on_started)
        self._worker.file_done.connect(self._on_done)
        self._worker.file_failed.connect(self._on_failed)
        self._worker.file_cancelled.connect(self._on_cancelled)
        self._worker.stage.connect(self._on_stage)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.finished_all.connect(self._on_finished)
        self._worker.start()

    def _cancel(self):
        if self._worker is not None:
            self._worker.cancel()
            self.cancel_button.setEnabled(False)
            self.progress.setFormat("Stopping...")
            self._set_status("Stopping — finishing the current page.", "busy")

    # -------------------------------------------------------------- results
    def _on_started(self, path):
        row = self._row_for(path)
        if row >= 0:
            self.table.item(row, self.COL_STATUS).setText("Working...")
            self._paint_row(row, "busy")

    # ORDER MATTERS: "Flattening at 120 DPI" contains "DPI", so the flatten
    # entry has to be tested before the resolution ones or it reads as a
    # plain re-compression - which is the one stage the operator most needs
    # named correctly, because it is the destructive one.
    _STAGE_WORDS = (
        ("Flatten", "Turning pages into pictures"),
        ("Removing hidden information", "Removing hidden information"),
        ("Cleaned only", "Removing hidden information"),
        ("Repacked scans", "Repacking the scans"),
        ("black & white", "Shrinking the scans"),
        ("greyscale", "Shrinking the scans"),
        ("DPI", "Shrinking the scans"),
        ("Saving", "Saving"),
        ("Writing", "Saving"),
        ("Reading", "Reading the file"),
    )

    @classmethod
    def _plain_stage(cls, text):
        """Engine tier names are jargon on a user's screen. Keep the page
        counter, replace the label."""
        tail = ""
        if ": page " in text:
            text, _, tail = text.partition(": ")
            tail = " — " + tail
        for needle, plain in cls._STAGE_WORDS:
            if needle.lower() in text.lower():
                return plain + tail
        return text + tail

    def _on_stage(self, path, text, done, total):
        frac = (done / total) if total else 0.0
        overall = (self._files_done + min(frac, 1.0)) / max(self._files_total, 1)
        self.progress.setValue(int(overall * 100))
        row = self._row_for(path)
        label = os.path.basename(path)
        plain = self._plain_stage(text)
        self.progress.setFormat(f"{label} — {plain}{self._eta(overall)}")
        self._set_status(plain, "busy")
        if row >= 0:
            self.table.item(row, self.COL_STATUS).setText(plain)

    def _eta(self, fraction):
        if not self._started_at or fraction <= 0.02:
            return ""
        elapsed = time.monotonic() - self._started_at
        remaining = elapsed / fraction - elapsed
        if remaining < 5:
            return ""
        if remaining < 90:
            return f"   (about {int(remaining)}s left)"
        return f"   (about {int(remaining / 60) + 1} min left)"

    def _on_file_progress(self, done, total):
        self._files_done = done
        self.progress.setValue(int(100 * done / max(total, 1)))

    def _on_done(self, path, result):
        self._results[path] = result
        row = self._row_for(path)
        if row < 0:
            return
        self.table.item(row, self.COL_AFTER).setText(self._mb(result.dst_bytes))
        self.table.item(row, self.COL_ACTION).setText(result.tier)
        if not result.under_ceiling and not result.ceiling_applied:
            self.table.item(row, self.COL_STATUS).setText(
                f"Flattened — {self._mb(result.dst_bytes)}, size limit not applied")
            self._paint_row(row, "ok")
        elif not result.under_ceiling:
            self.table.item(row, self.COL_STATUS).setText("Still too big — click for why")
            self._paint_row(row, "error")
        elif result.saved_pct < 1.0:
            self.table.item(row, self.COL_STATUS).setText(
                "Already under the limit — cleaned copy saved")
            self._paint_row(row, "ok")
        else:
            self.table.item(row, self.COL_STATUS).setText(
                f"Done — {result.saved_pct:.0f}% smaller")
            self._paint_row(row, "ok")
        # A voided signature is not a failure - the file optimized correctly -
        # so it is amber, not red: putting it next to genuine errors is how a
        # warning gets trained away. It overwrites whatever status was just
        # set, because "review before sending" outranks the percentage.
        if result.signature_voided:
            self.table.item(row, self.COL_STATUS).setText(
                self.table.item(row, self.COL_STATUS).text()
                + "  ·  SIGNATURE VOIDED — review before sending")
            self._paint_row(row, "warn")

    def _on_failed(self, path, message):
        self._failed[path] = message
        row = self._row_for(path)
        if row >= 0:
            self.table.item(row, self.COL_STATUS).setText(message)
            self._paint_row(row, "error")

    def _on_cancelled(self, path):
        row = self._row_for(path)
        if row >= 0:
            self.table.item(row, self.COL_STATUS).setText("Not done — you stopped the run")
            self._paint_row(row, "muted")

    def _paint_row(self, row, kind):
        """Colour is read from the CURRENT theme, never frozen at paint time -
        the same reason RenamerPanel repaints on a theme switch. Cancelled and
        not-yet-started rows are muted, so they cannot be mistaken for errors."""
        colour = QColor(theme_color({
            "ok": "green", "error": "red", "busy": "accent", "warn": "amber",
        }.get(kind, "muted")))
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item is not None:
                item.setForeground(colour)
                item.setData(Qt.UserRole + 1, kind)

    def refresh_results(self):
        """Called by MainWindow after a theme switch."""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_STATUS)
            if item is None:
                continue
            self._paint_row(row, item.data(Qt.UserRole + 1) or "muted")

    def _on_finished(self):
        try:
            win = self.window()
            if win is not None and not win.isActiveWindow():
                QApplication.alert(win, 0)
        except Exception:
            pass
        try:
            under = sum(1 for r in self._results.values()
                        if r.under_ceiling or not r.ceiling_applied)
            over = sum(1 for r in self._results.values()
                       if not r.under_ceiling and r.ceiling_applied)
            failed = len(self._failed)
            stopped = self.table.rowCount() - len(self._results) - failed
            saved = sum(max(r.src_bytes - r.dst_bytes, 0)
                        for r in self._results.values())

            voided = sum(1 for r in self._results.values() if r.signature_voided)

            summary = [f"Optimized: {under}"]
            if voided:
                summary.append(
                    f"SIGNATURE VOIDED — review before sending: {voided}")
            if over:
                summary.append(f"Still too big: {over}")
            if stopped > 0:
                summary.append(f"Not done (you stopped): {stopped}")
            if failed:
                summary.append(f"Could not be done: {failed}")
            if self._ignored:
                summary.append("Skipped (not a PDF): " + ", ".join(self._ignored[:6]))
            msg = "\n".join(summary)

            elapsed = time.monotonic() - (self._started_at or time.monotonic())
            tail = ""
            if over:
                tail = ("\nClick the row marked 'Still too big' — it explains "
                        "what is taking up the space and what to try next.")
            self.detail_label.setText(
                msg.replace("\n", "   |   ")
                + f"\nSpace saved: {self._mb(saved)}   |   Took "
                  f"{int(elapsed)}s"
                + f"\nOptimized copies are in an '{setting('optimized_dir_name')}' folder "
                  "inside each file's own folder. The originals were not "
                  "changed." + tail
            )
            if self._results and setting("optimizer_open_when_done") \
                    and not self._shutting_down:
                self._open_output_folder()
            if over or failed or voided:
                self._set_status("Completed with items needing attention.", "error")
                if not self._shutting_down:
                    Msg.warning(self, "Completed", msg)
            elif stopped > 0:
                self._set_status("Stopped.", "")
                if not self._shutting_down:
                    Msg.information(self, "Stopped", msg)
            else:
                self._set_status("Done!", "ok")
                if not self._shutting_down:
                    Msg.information(self, "Success", msg)
        finally:
            self._set_busy(False)                        # cleared no matter what
            self.progress.setFormat("")
            self._worker = None

    # ------------------------------------------------------------- outputs
    def _open_output_folder(self):
        folders = []
        for result in self._results.values():
            folder = os.path.dirname(result.dst)
            if folder not in folders:
                folders.append(folder)
        if not folders:
            return
        for folder in folders[:5]:                # a mixed drop can span several
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        if len(folders) > 5:
            Msg.information(
                self, "Output Folders",
                f"These files came from {len(folders)} different folders, so "
                "there are that many 'Optimized' folders. The first five were "
                "opened; the rest are listed beside each row.")

    def _open_row(self, row, _col):
        path = self._path_at(row)
        result = self._results.get(path)
        target = result.dst if result is not None else path
        if target and os.path.exists(target):
            open_file(target)

    def _table_menu(self, pos):
        menu = self._build_table_menu(pos)
        if menu is not None:
            menu.exec(self.table.viewport().mapToGlobal(pos))

    def _build_table_menu(self, pos):
        item = self.table.itemAt(pos)
        if item is None:
            return None
        row = item.row()
        path = self._path_at(row)
        if not path:
            return None
        result = self._results.get(path)
        menu = QMenu(self)
        if result is not None and result.dst and os.path.exists(result.dst):
            menu.addAction("Open optimized file", _later(self._open_row, row, 0))
            menu.addAction("Open output folder",
                           lambda: open_in_explorer(result.dst))
            menu.addAction("Copy optimized path",
                           lambda: QApplication.clipboard().setText(result.dst))
            menu.addSeparator()
        menu.addAction("Open original", lambda: open_file(path))
        menu.addAction("Open original's folder", lambda: open_in_explorer(path))
        menu.addAction("Copy original path",
                       lambda: QApplication.clipboard().setText(path))
        return menu

    def _save_results(self):
        if not (self._results or self._failed or self._reports):
            return
        default = os.path.join(
            os.path.dirname(self._last_paths[0]) if self._last_paths else "",
            "optimizer-results.csv")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save results", default, "CSV file (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                # "Signature voided" is its own column and is read from the
                # result field, not scraped from the Status text - a warning
                # that survives only as prose inside another cell is one
                # wording change away from disappearing from the record.
                fh.write("Source file,Folder,Before (MB),After (MB),"
                         "What it had to do,Signature voided,Status,Saved to\n")
                for row in range(self.table.rowCount()):
                    src = self._path_at(row) or ""
                    result = self._results.get(src)
                    cells = [
                        os.path.basename(src),
                        os.path.dirname(src),
                        f"{os.path.getsize(src) / 1e6:.2f}" if os.path.isfile(src) else "",
                        f"{result.dst_bytes / 1e6:.2f}" if result else "",
                        result.tier if result else "",
                        ("YES - REVIEW BEFORE SENDING"
                         if result and result.signature_voided else
                         "SIGNED - optimizing would void it"
                         if self._reports.get(src, {}).get("signed") else "no"),
                        self.table.item(row, self.COL_STATUS).text()
                        if self.table.item(row, self.COL_STATUS) else "",
                        result.dst if result else "",
                    ]
                    fh.write(",".join('"' + csv_safe(c).replace('"', '""') + '"'
                                      for c in cells) + "\n")
        except OSError as exc:
            Msg.warning(self, "Could Not Save",
                                describe_lock_failure(path, exc))
            return
        self._set_status(f"Results saved to {os.path.basename(path)}", "ok")

    def keyPressEvent(self, event):
        """Ctrl+C copies the selected rows, so a path can be pasted into an
        email or a note."""
        if event.matches(QKeySequence.Copy):
            rows = sorted({i.row() for i in self.table.selectedIndexes()})
            if rows:
                lines = []
                for row in rows:
                    src = self._path_at(row) or ""
                    result = self._results.get(src)
                    lines.append("\t".join([
                        src,
                        self.table.item(row, self.COL_ACTION).text()
                        if self.table.item(row, self.COL_ACTION) else "",
                        self.table.item(row, self.COL_STATUS).text()
                        if self.table.item(row, self.COL_STATUS) else "",
                        result.dst if result else "",
                    ]))
                QApplication.clipboard().setText("\n".join(lines))
                self._set_status(f"Copied {plural(len(rows), 'row')} to the clipboard", "ok")
                return
        super().keyPressEvent(event)

    def _show_detail(self, row, _col):
        path = self._path_at(row)
        result = self._results.get(path)
        report = self._reports.get(path)
        if report is not None:
            self.detail_label.setText(format_inspect_report(report))
            return
        if result is None:
            message = self._failed.get(path)
            if message:
                self.detail_label.setText(
                    f"{os.path.basename(path)}\n{path}\n\n{message}")
            else:
                self.detail_label.setText(
                    f"{os.path.basename(path)}\n{path}\n\nNot done yet.")
            return
        lines = [
            os.path.basename(result.src),
            f"{self._mb(result.src_bytes)}  →  {self._mb(result.dst_bytes)}"
            f"   ({result.saved_pct:.1f}% smaller)",
            f"What it had to do: {result.tier}",
            f"Saved to: {result.dst}",
        ]
        if result.diagnosis:
            lines += ["", "WHAT IS IN THIS FILE:"]
            lines += [f"   {ln}" for ln in result.diagnosis.splitlines()]
        lines += ["", "REMOVED:"]
        lines += ([f"   • {k}: {v}" for k, v in sorted(result.removed.items())]
                  or ["   • nothing hidden was found in this file"])
        lines += ["", "KEPT ON PURPOSE:"]
        lines += ([f"   • {k}" for k in sorted(result.kept)] or ["   • nothing"])
        if result.notes:
            lines += ["", "NOTES:"] + [f"   • {n}" for n in result.notes]
        self.detail_label.setText("\n".join(lines))



# =====================================================================
# Startup splash
# =====================================================================
#
# WHY A SPLASH AT ALL, and why it is where it is.
#
# MEASURED on this codebase, not assumed:
#     import PySide6.QtWidgets   4225 ms cold / 107 ms warm
#     import acrod           240 ms cold /  57 ms warm
#     apply_theme                 138 ms
#     all six panels              298 ms  (Bulk Renamer alone: 157 ms)
#
# The dominant cost is not Python - it is reading Qt's binaries off disk the
# first time. That is exactly what a frozen .exe pays on every cold launch,
# and it is far worse from a network share than from a local disk.
#
# The consequence for a splash screen: a Qt QSplashScreen CANNOT cover the
# expensive part, because showing one requires QApplication, which requires Qt
# to already be loaded. By the time a QSplashScreen can appear, the slow part
# is over. Only PyInstaller's native splash - drawn by the C bootloader before
# Python starts at all - covers the gap the user actually experiences.
#
# So both are used, each for the part it can reach:
#   * pyi_splash (frozen only) covers Qt loading and module import.
#   * QSplashScreen covers theme + window construction, and is what a
#     from-source run gets.


class Splash:
    """The PyInstaller native splash, and nothing else. Never fatal.

    WHY THERE IS NO Qt SPLASH HERE - measured, and the first attempt removed:

        import PySide6.QtWidgets   4225 ms cold /  107 ms warm
        import acrod           240 ms cold /   57 ms warm
        apply_theme                 138 ms
        all six panels              298 ms  (Bulk Renamer alone: 157 ms)
        window on screen after Qt is loaded:  32 ms

    The dominant cost is not Python. It is reading Qt's binaries off disk the
    first time - which is what a frozen .exe pays on every cold launch, and
    far worse from a network share than a local disk.

    A Qt QSplashScreen cannot cover any of that, because showing one requires
    QApplication, which requires Qt to already be loaded. By the time it can
    appear, the slow part is finished and only ~32ms remains. It was built,
    measured, and deleted: it covered 32ms of real work and its own show()
    measured 1006ms on the test platform, so it made startup visibly WORSE.

    What does work is PyInstaller's native splash: the C bootloader paints it
    before Python starts at all, which is the only code that runs during the
    part the user is actually waiting through. Nothing here runs from source,
    and that is correct - from source there is nothing to cover.
    """

    def __init__(self, app=None):
        self._pyi = None
        try:                                    # frozen build only
            import pyi_splash
            self._pyi = pyi_splash
        except Exception:
            pass                                # running from source

    def text(self, message: str):
        try:
            if self._pyi is not None:
                self._pyi.update_text(message)
        except Exception:
            pass                                # a splash is never worth a crash

    def close(self, window=None):
        try:
            if self._pyi is not None:
                self._pyi.close()
        except Exception:
            pass
        self._pyi = None


class AdaptiveTabBar(QTabBar):
    """Tab bar that fits the window instead of hiding tabs behind scroll
    arrows. Full labels -> tighter padding ->
    icon-only for the tabs not selected (full name stays as the tooltip).
    The real titles live in self._titles, so code that needs a tab's name
    must call title(i), because a compact tab's own text is empty."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._titles = []
        self._mode = None
        self._fitting = False
        self.setExpanding(False)     # packed, left-aligned; width is managed by _fit
        self.setDrawBase(False)
        self.currentChanged.connect(self._on_current_changed)
        if parent is not None:
            # A packed bar keeps its own width when the window grows, so it
            # never sees the room come back; watch the container instead.
            parent.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize and obj is self.parentWidget():
            self._fit()
        return super().eventFilter(obj, event)

    def title(self, i):
        return self._titles[i] if 0 <= i < len(self._titles) else self.tabText(i)

    def tabInserted(self, i):
        self._titles.insert(i, self.tabText(i))
        super().tabInserted(i)
        self.setTabToolTip(i, self._titles[i])
        QTimer.singleShot(0, self._fit)

    def tabRemoved(self, i):
        if 0 <= i < len(self._titles):
            del self._titles[i]
        super().tabRemoved(i)

    def _apply(self, mode):
        self.setProperty("density", "normal" if mode == "full" else "tight")
        self.style().unpolish(self)
        self.style().polish(self)
        cur = self.currentIndex()
        for i, t in enumerate(self._titles):
            want = t if (mode != "compact" or i == cur) else ""
            if self.tabText(i) != want:
                self.setTabText(i, want)
        self._mode = mode

    def _avail(self):
        par = self.parentWidget()
        return par.width() if par is not None and par.width() > 50 else self.width()

    def _on_current_changed(self, _index=0):
        self._fit()

    def _fit(self):
        if self._fitting or not self._titles:
            return
        self._fitting = True
        try:
            avail = self._avail()
            for mode in ("full", "tight", "compact"):
                self._apply(mode)
                if mode == "compact" or QTabBar.sizeHint(self).width() <= avail:
                    break
        finally:
            self._fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def changeEvent(self, event):
        super().changeEvent(event)
        if not self._fitting and event.type() in (QEvent.StyleChange, QEvent.FontChange):
            QTimer.singleShot(0, self._fit)


# ---------------------------------------------------------------------
# Page Organizer tab: pick a folder, click a PDF, see its pages, reorder /
# rotate / remove / extract, then Save & Next. Output never replaces the
# original unless Settings says so. All PDF work is in organize_pdf /
# extract_pages (core region); this file only builds the page plan.
# ---------------------------------------------------------------------
def _page_looks_blank(pil) -> bool:
    """True when a page image has essentially no ink. Averaged down first so
    scanner speckle does not count as ink. A SUGGESTION only: the operator
    still decides which pages go."""
    small = pil.convert("L").resize((max(1, pil.width // 3), max(1, pil.height // 3)))
    hist = small.histogram()
    ink = sum(hist[:200])
    return ink / float(max(1, small.width * small.height)) < 0.002


# A QThread destroyed while it still runs aborts or segfaults. Panels are
# dropped (closed, garbage-collected) while a rescan may be mid-flight, so every
# worker is also held here until it has really stopped.
_ALIVE_WORKERS = set()


def _reap_alive_workers():
    for w in [w for w in _ALIVE_WORKERS if not w.isRunning()]:
        _ALIVE_WORKERS.discard(w)


class _OrgWorker(QThread):
    """A cancellable background job. Never touches widgets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = False

    def cancel(self):
        self._stop = True


class _PreviewWorker(_OrgWorker):
    """Builds a temporary PDF of the pages as they would be saved (pasted pages included), so the
    reader can show them full size. The result is only ever read."""
    done = Signal(int, str)
    failed = Signal(int, str)

    def __init__(self, plan, sources, epoch, parent=None):
        super().__init__(parent)
        self._plan, self._sources, self._epoch = plan, list(sources), epoch

    def run(self):
        fd, tmp = tempfile.mkstemp(prefix="acrod_preview_", suffix=".pdf")
        os.close(fd)
        ok = False
        try:
            readers = []
            for p in self._sources:
                require_safe_to_expand(p)
                readers.append(_open_reader(p))
            if self._stop:
                return
            _build_from_multi_plan(readers, self._plan, tmp)
            ok = True
        except Exception as e:
            log_error_detail(e, "reader preview")
            if not self._stop:
                self.failed.emit(self._epoch, plain_error(e, ""))
        finally:
            if ok and not self._stop:
                self.done.emit(self._epoch, tmp)
            else:
                try:
                    os.remove(tmp)
                except OSError:
                    pass


class _ThumbWorker(_OrgWorker):
    total = Signal(int, int)               # token, page count
    thumb = Signal(int, int, object, bool)  # token, index, QImage, looks_blank
    failed = Signal(int, str)

    def __init__(self, path, width, token, parent=None):
        super().__init__(parent)
        self._path, self._width, self._token = path, width, token

    def run(self):
        try:
            require_safe_to_expand(self._path)
            pdfium = _pdfium()
            with _PDFIUM_LOCK:
                doc = pdfium_open_for_render(pdfium, self._path)
                n = len(doc)
            self.total.emit(self._token, n)
            try:
                for i in range(n):
                    if self._stop:
                        break
                    with _PDFIUM_LOCK:
                        page = doc[i]
                        bitmap = None
                        try:
                            w_pt, _h = page.get_size()
                            bitmap = page.render(scale=render_scale(
                                w_pt, _h, self._width / max(float(w_pt), 1.0),
                                THUMB_MAX_PX, THUMB_MAX_SIDE))
                            pil = bitmap.to_pil().convert("RGB")
                        finally:
                            for handle in (bitmap, page):
                                try:
                                    if handle is not None:
                                        handle.close()
                                except Exception:
                                    pass
                    blank = _page_looks_blank(pil)
                    img = QImage(pil.tobytes("raw", "RGB"), pil.width, pil.height,
                                 pil.width * 3, QImage.Format_RGB888).copy()
                    self.thumb.emit(self._token, i, img, blank)
            finally:
                with _PDFIUM_LOCK:
                    doc.close()
        except Exception as e:
            msg = str(e)
            if "password" in msg.lower():
                msg = "This PDF needs a password to open."
            self.failed.emit(self._token, msg or "This PDF could not be read.")


ORG_ZOOM_MIN, ORG_ZOOM_MAX, ORG_ZOOM_STEP = 90, 800, 1.2    # thumbnail width limits and the factor per wheel notch
ORG_LAZY_W = 300                    # wider than this, only pages near the window keep a full-size picture
ORG_LIVE_ROWS = 8                   # rows kept either side of the window while zoomed far in
ORG_HI_MAX_W = 1800                 # widest sharper picture drawn for a page in view
ORG_HI_MAX_PAGES = 16               # sharper pictures kept at once (memory stays bounded)
PASTE_BASE = 1_000_000_000          # page "numbers" at or above this are pasted pages (not pages of the open file)
CLIP_MAX_PAGES = 300                # the most pages one Copy / Cut keeps
PASTE_PAGES_MAX = 5000              # the most pages a PDF built in the Page Organizer may have


class PageClipboard:
    """Pages Cut or Copied in the Page Organizer. Held in memory only (not kept when the app closes).
    Each entry remembers WHICH FILE and WHICH PAGE it came from, the rotation it had and a small
    picture, so it can be pasted into any other PDF without the source being open."""

    def __init__(self):
        self.items = []
        self.cut = None          # set by a Cut: the open PDF's cut pages {"owner", "tokens"}; a Copy clears it

    def set(self, items, cut=None):
        self.items = list(items)[:CLIP_MAX_PAGES]
        self.cut = cut
        # Mark the system clipboard as ours, so a PDF file copied in Explorer AFTER this (which replaces the
        # mark) is told apart from these pages when Ctrl+V is pressed.
        try:
            if self.items and QApplication.instance() is not None:
                md = QMimeData()
                md.setData(PAGE_CLIP_MIME, QByteArray(str(len(self.items)).encode("ascii")))
                QApplication.clipboard().setMimeData(md)
        except Exception:
            pass                                  # the in-app copy still works without it

    def clear(self):
        self.items = []
        self.cut = None

    def __len__(self):
        return len(self.items)


PAGE_CLIP_MIME = "application/x-acrod-page-clip"
_PAGE_CLIP = PageClipboard()


def clipboard_pdf_paths():
    """PDF files on the system clipboard that were copied OUTSIDE the Page Organizer (Explorer's Copy), else [].
    Pages copied in the Organizer carry PAGE_CLIP_MIME and are not files."""
    try:
        md = QApplication.clipboard().mimeData()
    except Exception:
        return []
    if md is None or md.hasFormat(PAGE_CLIP_MIME) or not md.hasUrls():
        return []
    return [u.toLocalFile() for u in md.urls() if u.isLocalFile() and u.toLocalFile().lower().endswith(".pdf")]


class _PageImageWorker(_OrgWorker):
    """Draws small pictures for pasted pages whose picture was not loaded when they were copied."""
    image = Signal(int, int, object)       # epoch, page number (token), QImage

    def __init__(self, jobs, width, epoch, parent=None):
        super().__init__(parent)
        self._jobs, self._width, self._epoch = list(jobs), width, epoch     # jobs: [(token, path, source_page)]

    def run(self):
        by_path = {}
        for tok, path, sidx in self._jobs:
            by_path.setdefault(path, []).append((tok, sidx))
        for path, items in by_path.items():
            doc = None
            try:
                require_safe_to_expand(path)
                pdfium = _pdfium()
                with _PDFIUM_LOCK:
                    doc = pdfium_open_for_render(pdfium, path)
                if not self._draw(doc, items):
                    return
            except Exception:
                continue
            finally:
                if doc is not None:
                    try:
                        with _PDFIUM_LOCK:
                            doc.close()
                    except Exception:
                        pass

    def _draw(self, doc, items) -> bool:
        """Render each (token, source page) of an open document; False if cancelled."""
        for tok, sidx in items:
            if self._stop:
                return False
            try:
                with _PDFIUM_LOCK:
                    page = doc[sidx]
                    bitmap = None
                    try:
                        w_pt, h_pt = page.get_size()
                        bitmap = page.render(scale=render_scale(
                            w_pt, h_pt, self._width / max(float(w_pt), 1.0),
                            THUMB_MAX_PX, THUMB_MAX_SIDE))
                        pil = bitmap.to_pil().convert("RGB")
                    finally:
                        for handle in (bitmap, page):
                            try:
                                if handle is not None:
                                    handle.close()
                            except Exception:
                                pass
                img = QImage(pil.tobytes("raw", "RGB"), pil.width, pil.height,
                             pil.width * 3, QImage.Format_RGB888).copy()
                self.image.emit(self._epoch, tok, img)
            except Exception:
                continue                      # one page that will not draw leaves its grey square
        return True


B_PAGES_MAX = 500


class _BWorker(_PageImageWorker):
    """The B pane's renderer: opens one PDF, reports how many pages it has, then draws
    each page's picture. Separate from the A pane's workers so switching A's file never
    cancels it."""
    total = Signal(int, int)               # epoch, page count
    failed = Signal(int, str)              # epoch, plain-language reason

    def __init__(self, path, width, epoch, parent=None):
        super().__init__([], width, epoch, parent)
        self._path = path

    def run(self):
        doc = None
        try:
            problem = pdf_file_problem(self._path) or pdf_expansion_problem(self._path)
            if problem:
                self.failed.emit(self._epoch, problem)
                return
            require_safe_to_expand(self._path)
            pdfium = _pdfium()
            with _PDFIUM_LOCK:
                doc = pdfium_open_for_render(pdfium, self._path)
                n = len(doc)
            if n == 0:
                self.failed.emit(self._epoch, f"'{os.path.basename(self._path)}' has no pages.")
                return
            self.total.emit(self._epoch, n)
            self._draw(doc, [(i, i) for i in range(min(n, B_PAGES_MAX))])
        except PdfOpError as e:
            self.failed.emit(self._epoch, str(e))
        except Exception as e:
            self.failed.emit(self._epoch, plain_error(e, self._path))
        finally:
            if doc is not None:
                try:
                    with _PDFIUM_LOCK:
                        doc.close()
                except Exception:
                    pass


class _InfoWorker(_OrgWorker):
    info = Signal(int, str, int)           # token, path, pages (-1 = unreadable)

    def __init__(self, paths, token, parent=None):
        super().__init__(parent)
        self._paths, self._token = paths, token

    def run(self):
        pdfium = _pdfium()
        for path in self._paths:
            if self._stop:
                break
            try:
                with _PDFIUM_LOCK:
                    doc = pdfium.PdfDocument(path)
                    try:
                        n = len(doc)
                    finally:
                        doc.close()
            except Exception:
                n = -1
            self.info.emit(self._token, path, n)


class _ListWorker(_OrgWorker):
    """Re-reads the folder listing off the UI thread (a network share can be slow)."""
    listed = Signal(int, str, object)      # token, folder, pdf_folder_listing()

    def __init__(self, folder, token, parent=None):
        super().__init__(parent)
        self._folder, self._token = folder, token

    def run(self):
        listing = pdf_folder_listing(self._folder)
        if not self._stop:
            self.listed.emit(self._token, self._folder, listing)


class _SearchWorker(_OrgWorker):
    result = Signal(int, str, object)      # token, path, search_pdf_text() dict
    finished_all = Signal(int)

    def __init__(self, paths, needle, token, parent=None):
        super().__init__(parent)
        self._paths, self._needle, self._token = paths, needle, token

    def run(self):
        for path in self._paths:
            if self._stop:
                break
            try:
                r = search_pdf_text(path, self._needle)
            except Exception:
                r = {"pages": [], "hits": 0, "has_text": False, "error": True}
            self.result.emit(self._token, path, r)
        self.finished_all.emit(self._token)


class _SaveWorker(_OrgWorker):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, kind, path, stamp, payload, replace, parent=None, extra=None, extra_stamps=None, texts=None,
                 keep_signatures=False, strip_own=True):
        super().__init__(parent)
        self._keep_sig = bool(keep_signatures)
        self._strip_own = bool(strip_own)
        self._kind, self._path, self._stamp = kind, path, stamp
        self._payload, self._replace = payload, replace
        self._extra, self._extra_stamps = extra, extra_stamps
        self._texts = texts

    def run(self):
        try:
            if self._kind == "extract":
                res = extract_pages(self._path, self._payload["indices"], expect=self._stamp,
                                    dest_dir=self._payload.get("dest_dir"),
                                    file_name=self._payload.get("file_name"))
            else:
                res = organize_pdf(self._path, self._payload, expect=self._stamp,
                                   replace=self._replace, extra=self._extra,
                                   extra_expect=self._extra_stamps, texts=self._texts,
                                   keep_signatures=self._keep_sig, strip_own=self._strip_own)
            res["kind"] = self._kind
            self.done.emit(res)
        except PdfOpError as e:
            self.failed.emit(str(e))
        except OSError as e:
            self.failed.emit(describe_lock_failure(self._path, e))
        except Exception as e:
            self.failed.emit(plain_error(e, self._path))


class FlowLayout(QLayout):
    """Lays widgets left to right on ONE line and wraps to the next only when
    the width cannot hold them, so a toolbar never clips a label."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        w = h = 0
        for it in self._items:
            sz = it.minimumSize()
            w, h = max(w, sz.width()), max(h, sz.height())
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def _do(self, rect, test):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = r.x(), r.y(), 0
        for it in self._items:
            sz = it.sizeHint()
            if x + sz.width() > r.right() + 1 and line_h > 0:
                x, y, line_h = r.x(), y + line_h + self._sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), sz))
            x += sz.width() + self._sp
            line_h = max(line_h, sz.height())
        return y + line_h - rect.y() + m.bottom()


class _PageGrid(QListWidget):
    """Thumbnail grid. Drag the selected pages to a new place: a gold bar
    shows where they will land. The drop does not move items itself - it
    reports (rows, target) through `pagesDropped` and the panel reorders its
    own page list, so the grid can never disagree with what will be saved."""
    pagesDropped = Signal(object, int)          # source rows, insertion index
    deleteRequested = Signal()
    cutRequested = Signal()
    copyRequested = Signal()
    pasteRequested = Signal(bool)               # True = paste BEFORE the selected page
    foreignDropped = Signal(object, int, int)   # pages dragged in from the B pane: B rows, insertion index, B serial
    filesDropped = Signal(object, int)          # PDF files dragged in (list, tray, Explorer): paths, insertion index
    zoomRequested = Signal(int)                 # +1 / -1 per wheel notch or key press: bigger / smaller thumbnails
    zoomReset = Signal()
    MIME = "application/x-acrod-pages"
    MIME_B = "application/x-acrod-pages-b"

    def __init__(self):
        super().__init__()
        self.setObjectName("pageGrid")
        self.setViewMode(QListWidget.IconMode)
        self.setMovement(QListWidget.Static)
        self.setResizeMode(QListWidget.Adjust)
        self.setFlow(QListWidget.LeftToRight)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSpacing(8)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setAccessibleName("Pages of the selected PDF")
        self._drop_at = None                    # insertion index being shown
        self._wheel_acc = 0

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self._wheel_acc += event.angleDelta().y()
            while abs(self._wheel_acc) >= 120:            # one step per notch; a smooth touchpad adds up to one
                self.zoomRequested.emit(1 if self._wheel_acc > 0 else -1)
                self._wheel_acc -= 120 if self._wheel_acc > 0 else -120
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selectedItems():
            self.deleteRequested.emit()
            event.accept()
            return
        mods = event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier)
        if mods in (Qt.ControlModifier, Qt.ControlModifier | Qt.ShiftModifier):
            key = event.key()
            if key in (Qt.Key_Plus, Qt.Key_Equal):
                self.zoomRequested.emit(1); event.accept(); return
            if key == Qt.Key_Minus:
                self.zoomRequested.emit(-1); event.accept(); return
            if key == Qt.Key_0 and mods == Qt.ControlModifier:
                self.zoomReset.emit(); event.accept(); return
            if key == Qt.Key_X and mods == Qt.ControlModifier:
                self.cutRequested.emit(); event.accept(); return
            if key == Qt.Key_C and mods == Qt.ControlModifier:
                self.copyRequested.emit(); event.accept(); return
            if key == Qt.Key_V:
                self.pasteRequested.emit(mods != Qt.ControlModifier); event.accept(); return
        super().keyPressEvent(event)

    # --- what is dragged
    def mimeData(self, items):
        mime = QMimeData()
        rows = sorted(self.row(i) for i in items)
        mime.setData(self.MIME, QByteArray(",".join(str(r) for r in rows).encode("ascii")))
        return mime

    def mimeTypes(self):
        return [self.MIME]

    def supportedDropActions(self):
        # COPY, never Move. A drag that ends as a Move makes Qt delete the
        # dragged items from the view itself once the drop is accepted - and
        # by then the panel has rebuilt the grid, so it deleted the pages that
        # had just landed. The panel owns the page list; the grid only reports.
        return Qt.CopyAction

    def supportedDragActions(self):
        return Qt.CopyAction

    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if not items:
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData(items))
        first = items[0]
        icon = first.icon()
        if not icon.isNull():
            pm = icon.pixmap(self.iconSize())
            drag.setPixmap(pm.scaledToWidth(max(48, pm.width() // 2), Qt.SmoothTransformation))
        drag.exec(Qt.CopyAction, Qt.CopyAction)

    # --- where it would land
    def insertion_index(self, pos) -> int:
        n = self.count()
        if n == 0:
            return 0
        item = self.itemAt(pos)
        if item is None:
            # Nearest page: a gap between pages, or the empty area after the last.
            best, best_d = None, None
            for r in range(n):
                c = self.visualItemRect(self.item(r)).center()
                d = (c.x() - pos.x()) ** 2 + 4 * (c.y() - pos.y()) ** 2
                if best_d is None or d < best_d:
                    best, best_d = r, d
            item = self.item(best)
        row = self.row(item)
        rect = self.visualItemRect(item)
        return row if pos.x() < rect.center().x() else row + 1

    def _ours(self, event) -> bool:
        md = event.mimeData()
        return md.hasFormat(self.MIME) or md.hasFormat(self.MIME_B)

    def _pdf_files(self, event):
        """Local .pdf paths in a file drag. Only while pages are showing: with nothing open, a dropped
        file goes to the panel, which opens it."""
        md = event.mimeData()
        if not self.count() or not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile() and u.toLocalFile().lower().endswith(".pdf")]

    def dragEnterEvent(self, event):
        if self._ours(event) or self._pdf_files(event):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()                      # other files dropped from Explorer go to the panel

    def dragMoveEvent(self, event):
        if not (self._ours(event) or self._pdf_files(event)):
            event.ignore()
            return
        self._drop_at = self.insertion_index(event.position().toPoint())
        self.viewport().update()
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event):
        self._drop_at = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        files = [] if self._ours(event) else self._pdf_files(event)
        if not (self._ours(event) or files):
            event.ignore()
            return
        target = self.insertion_index(event.position().toPoint())
        self._drop_at = None
        self.viewport().update()
        if files:
            event.setDropAction(Qt.CopyAction)
            event.accept()
            self.filesDropped.emit(files, target)
            return
        if event.mimeData().hasFormat(self.MIME_B):
            try:
                serial, _sep, body = bytes(event.mimeData().data(self.MIME_B)).decode("ascii").partition(":")
                rows = [int(x) for x in body.split(",") if x]
                serial = int(serial)
            except ValueError:
                event.ignore()
                return
            event.setDropAction(Qt.CopyAction)
            event.accept()
            self.foreignDropped.emit(rows, target, serial)
            return
        try:
            rows = [int(x) for x in bytes(event.mimeData().data(self.MIME)).decode("ascii").split(",") if x]
        except ValueError:
            event.ignore()
            return
        event.setDropAction(Qt.CopyAction)
        event.accept()
        self.pagesDropped.emit(rows, target)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_at is None or not self.count():
            return
        n = self.count()
        if self._drop_at >= n:
            r = self.visualItemRect(self.item(n - 1))
            x = r.right() + 4
        else:
            r = self.visualItemRect(self.item(self._drop_at))
            x = r.left() - 4
        painter = QPainter(self.viewport())
        pen = QPen(QColor(theme_color("accent")))
        pen.setWidth(4)
        painter.setPen(pen)
        painter.drawLine(x, r.top() + 2, x, r.bottom() - 2)
        painter.end()


class _BGrid(_PageGrid):
    """The B pane's pages: a read-only SOURCE. Pages can be picked, copied and dragged into the
    A grid; nothing can be dropped here, moved, deleted or turned."""
    serial = 0                                   # which B document these rows belong to

    def __init__(self):
        super().__init__()
        self.setObjectName("pageGridB")
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setAccessibleName("Pages of the B PDF (a source: copy or drag pages into the A PDF)")
        self.setToolTip("Pages of the B PDF. Click to select, then drag into the A pages, double-click to insert, or right-click.")

    def mimeData(self, items):
        mime = QMimeData()
        rows = sorted(self.row(i) for i in items)
        mime.setData(self.MIME_B, QByteArray(f"{self.serial}:{','.join(str(r) for r in rows)}".encode("ascii")))
        return mime

    def mimeTypes(self):
        return [self.MIME_B]

    def dragEnterEvent(self, event):
        event.ignore()

    def dragMoveEvent(self, event):
        event.ignore()

    def dropEvent(self, event):
        event.ignore()


class _FileDragList(QListWidget):
    """The Page Organizer's list of PDFs. A row drags out as an ordinary file: onto the B pane to compare it, onto the
    file tray, onto another tool's drop zone, or into Explorer. Dragging never removes or changes anything."""

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)

    def mimeData(self, items):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(p) for p in (it.data(Qt.UserRole) for it in items) if p])
        return md

    def startDrag(self, supported):
        item = self.currentItem()
        items = self.selectedItems() or ([item] if item is not None else [])
        if not items:
            return
        drag = QDrag(self)
        drag.setMimeData(self.mimeData(items))
        drag.exec(Qt.CopyAction, Qt.CopyAction)


class _ComparePane(QFrame):
    """B: a second PDF shown beside the one being built. Read only - it is where pages
    come FROM. The panel owns the data; this draws it and reports what the person asked."""
    chooseRequested = Signal()
    swapRequested = Signal()
    copyRequested = Signal()
    insertRequested = Signal(bool)              # True = before the selected A page
    closeRequested = Signal()
    pathsDropped = Signal(object)               # files dragged onto the pane (from the list, the tray or Explorer)

    def __init__(self):
        super().__init__()
        self.setObjectName("panel")
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.title = QLabel("B: no PDF chosen")
        self.title.setObjectName("sectionTitle")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        bar = FlowLayout(spacing=6)
        self.buttons = {}
        for key, text, tip, sig in (
                ("choose", "Choose PDF\u2026", "Pick the PDF to take pages from", self.chooseRequested),
                ("copy", "Copy pages", "Copy the selected B pages; paste them into any PDF with Ctrl+V", self.copyRequested),
                ("insert", "Insert into A", "Put the selected B pages into the A PDF after its selected page (or at its end)",
                 None),
                ("swap", "Swap A \u21c6 B", "Make B the PDF you are building and show the current A here", self.swapRequested),
                ("close", "Close B", "Hide this pane", self.closeRequested)):
            b = QPushButton(text)
            b.setObjectName("secondary")
            b.setToolTip(tip)
            b.setStyleSheet("padding-left: 10px; padding-right: 10px;")
            if sig is not None:
                b.clicked.connect(sig)
            else:
                b.clicked.connect(self._emit_insert)
            self.buttons[key] = b
            bar.addWidget(b)
        lay.addLayout(bar)
        self.grid = _BGrid()
        lay.addWidget(self.grid, stretch=1)
        self.note = QLabel("Drop a PDF anywhere on this pane to show it here (from the list on the left, the file tray or "
                           "Explorer). Then drag pages from here into the A PDF, or select them and press Insert into A.")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.set_enabled_state(False)

    def _emit_insert(self):
        self.insertRequested.emit(False)

    @staticmethod
    def _urls(event):
        md = event.mimeData()
        return [u.toLocalFile() for u in md.urls() if u.toLocalFile()] if md.hasUrls() else []

    def _hover(self, on: bool):
        self.setStyleSheet(f"QFrame#panel {{ border: 2px dashed {theme_color('accent')}; }}" if on else "")

    def dragEnterEvent(self, event):
        if self._urls(event):
            event.acceptProposedAction()
            self._hover(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._hover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._hover(False)
        paths = self._urls(event)
        if paths:
            event.acceptProposedAction()
            self.pathsDropped.emit(paths)
        else:
            event.ignore()

    def set_enabled_state(self, has_doc: bool):
        for k in ("copy", "insert"):
            self.buttons[k].setEnabled(has_doc)


# ---------------------------------------------------------------------
# PDF reader view: the Page Organizer's full-size "open it like a PDF reader"
# mode. It shows the pages the organizer is BUILDING (plan order, plan
# rotation), so rotating, removing, reordering and undoing in here are the same
# operations as in the grid. All geometry and caching rules live in the reader
# engine above; this file draws them and runs the background renderer.
# ---------------------------------------------------------------------
class _ReaderWorker(_OrgWorker):
    """Renders pages for the reader. One per opened document, fed through a
    ReaderQueue. Small PDFs are read into memory once (no file lock is held, and
    the pages shown are the pages that were opened even if the file changes);
    a large one is opened per request so it never holds a Windows file lock."""
    opened = Signal(object, object)           # queue, [(w_pt, h_pt)]
    rendered = Signal(object, object, object)  # queue, key, QImage or None
    found = Signal(object, object, object)    # queue, key, [fraction boxes]
    laid = Signal(object, object, object)     # queue, key, [text runs of one page] (None when it could not be read)
    failed = Signal(object, str)

    def __init__(self, path, queue):
        super().__init__(None)
        self._path, self._queue = path, queue

    def cancel(self):
        super().cancel()
        self._queue.close()

    def run(self):
        q = self._queue
        doc = None
        try:
            require_safe_to_expand(self._path)
            pdfium = _pdfium()
            data = None
            try:
                if os.path.getsize(self._path) <= READER_MEM_FILE_MAX:
                    with open(self._path, "rb") as fh:
                        data = fh.read()
            except OSError:
                data = None
            src = data if data is not None else self._path
            if data is None:
                try:
                    st = os.stat(self._path)
                    self._stamp = (st.st_mtime_ns, st.st_size)
                except OSError:
                    self._stamp = None
            with _PDFIUM_LOCK:
                doc = pdfium_open_for_render(pdfium, src)
                n = len(doc)
            sizes = []
            for i in range(n):
                if self._stop:
                    return
                try:
                    with _PDFIUM_LOCK:
                        pg = doc[i]
                        try:
                            w, h = pg.get_size()
                        finally:
                            pg.close()
                    w, h = float(w), float(h)
                except Exception:           # one broken page must not hide the rest
                    w, h = 612.0, 792.0
                sizes.append((w, h))
            if data is None:                        # large file: do not keep it open
                with _PDFIUM_LOCK:
                    doc.close()
                doc = None
            self.opened.emit(q, sizes)
            while not self._stop:
                item = q.pop(0.5)
                if item is None:
                    continue
                _gen, key, payload = item
                kind = payload[0]
                try:
                    if kind == "render":
                        _k, idx, scale, rot = payload
                        pil = self._with_doc(doc, pdfium, lambda d: reader_render_page(d, idx, scale, rot))
                        img = QImage(pil.tobytes("raw", "RGB"), pil.width, pil.height,
                                     pil.width * 3, QImage.Format_RGB888).copy()
                        self.rendered.emit(q, key, img)
                    elif kind == "layout":
                        _k, idx = payload
                        runs = self._with_doc(doc, pdfium, lambda d: reader_text_layout(d, idx))
                        self.laid.emit(q, key, runs)
                    else:
                        _k, idx, needle, rot = payload
                        boxes = self._with_doc(doc, pdfium, lambda d: reader_find_boxes(d, idx, needle, rot))
                        self.found.emit(q, key, boxes)
                except Exception:
                    if kind == "render":
                        self.rendered.emit(q, key, None)
                    elif kind == "layout":
                        self.laid.emit(q, key, None)
                    else:
                        self.found.emit(q, key, [])
        except Exception as e:
            msg = str(e)
            if "password" in msg.lower():
                msg = "This PDF needs a password to open."
            self.failed.emit(q, msg or "This PDF could not be read.")
        finally:
            if doc is not None:
                try:
                    with _PDFIUM_LOCK:
                        doc.close()
                except Exception:
                    pass

    def _with_doc(self, doc, pdfium, fn):
        if doc is not None:
            with _PDFIUM_LOCK:
                return fn(doc)
        stamp = getattr(self, "_stamp", None)
        if stamp is not None:
            try:
                st = os.stat(self._path)
                now = (st.st_mtime_ns, st.st_size)
            except OSError:
                now = None
            if now != stamp:                        # never draw a different file against the sizes and plan we opened
                raise RuntimeError("The file changed on disk after it was opened.")
        with _PDFIUM_LOCK:
            d = pdfium_open_for_render(pdfium, self._path)
            try:
                return fn(d)
            finally:
                d.close()


_TEXT_STYLE_KEYS = ("family", "size", "bold", "italic", "underline", "color", "align")
_TEXT_MIME = "application/x-acrod-textbox"


def _same_text_edit(a, b) -> bool:
    """Two boxes that would put the same words in the same place in the same look."""
    keys = ("x", "y", "w", "text", "family", "size", "bold", "italic", "underline", "color", "align", "spacing")
    return all(getattr(a, k) == getattr(b, k) for k in keys)


class _TextBoxEditor(QPlainTextEdit):
    """The box you type into on a page. Ctrl+B / Ctrl+I / Ctrl+U are style keys; leaving it saves what you typed."""
    committed = Signal()
    formatKey = Signal(str)

    def keyPressEvent(self, e):
        names = {Qt.Key_B: "bold", Qt.Key_I: "italic", Qt.Key_U: "underline"}
        if e.modifiers() == Qt.ControlModifier and e.key() in names:
            self.formatKey.emit(names[e.key()])
            e.accept()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if e.reason() != Qt.PopupFocusReason:          # the editor's own Cut / Copy / Paste menu is not "done"
            self.committed.emit()


class _ReaderCanvas(QAbstractScrollArea):
    """The scrolling page stack. Draws only the pages on screen, asks the worker
    for the ones it lacks (nearest first) and keeps a pixel-bounded cache."""
    pageChanged = Signal(int)
    selectionChanged = Signal()
    contextRequested = Signal(object)
    scaleChanged = Signal(float)
    findChanged = Signal(int, int, bool)       # current hit number (0 = none), total, search finished
    loaded = Signal(int)
    loadFailed = Signal(str)
    deleteRequested = Signal()
    zoomKey = Signal(int)                      # +1 in, -1 out, 0 fit page, 100 actual size, 101 fit width
    rotateKey = Signal(int)
    goToKey = Signal()
    findKey = Signal()
    textsChanged = Signal(int, object)         # source page index, its new tuple of TextBox
    textHint = Signal(str)
    textStyleChanged = Signal()
    textToolKey = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("readerCanvas")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("PDF pages")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context)
        self.verticalScrollBar().setSingleStep(48)
        self.horizontalScrollBar().setSingleStep(48)
        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self.horizontalScrollBar().valueChanged.connect(self._on_scrolled)
        self._req_timer = QTimer(self)
        self._req_timer.setSingleShot(True)
        self._req_timer.setInterval(25)
        self._req_timer.timeout.connect(self._request_visible)
        self._path = ""
        self._queue = None
        self._worker = None
        self._retired = []                 # cancelled workers that may still be finishing
        self._sizes = None                 # [(w_pt, h_pt)] per SOURCE page index, None until opened
        self._pages = []                   # [(source_idx, extra_rotation)] in display order
        self._lay = ReaderLayout([], 1.0)
        self._mode = READER_FIT_WIDTH
        self._scale = 1.0
        self._cache = ReaderCache(self._budget_px())
        self._fallback = {}                # source_idx -> (rot, QImage): last picture, shown while a sharper one renders
        self._failed = set()
        self._sel = set()                  # source indices (stable across a reorder)
        self._anchor_pos = None
        self._press = None
        self._panned = False
        self.viewport().setCursor(Qt.OpenHandCursor)
        self._message = "Choose a PDF."
        self._relaying = False
        self._last_page = -1
        self._needle = ""
        self._hits = {}                    # (idx, rot) -> [fraction boxes]
        self._find_waiting = 0
        self._find_cursor = None           # (idx, k)
        self._find_jump = False
        self._tool_text = False            # the Text tool is on
        self._texts = {}                   # source page index -> tuple of TextBox
        self._tsel = None                  # (source page index, box number) of the selected box
        self._tdrag = None                 # a box being moved or resized
        self._ctx = None                   # what the last right-click landed on
        self._editor = None                # the typing box, built when first needed
        self._edit = None                  # (idx, k) of the box being typed into
        self._layouts = {}                 # source page index -> text runs found on it (read only when the Text tool needs them)
        self._layout_asked = set()
        self._hover_seg = None             # (idx, run) under the mouse
        self._pending_click = None         # a click that arrived before its page's text was read
        self._fresh = None                 # (idx, k, box): an old-text edit just begun, dropped again if nothing changed
        self._region_armed = False         # the next drag marks an area of the page whose picture of text is to be replaced
        self._region_drag = None           # {"pos", "a", "b"} while that area is being dragged
        self._text_style = {k: getattr(TextBox(), k) for k in _TEXT_STYLE_KEYS}

    # ------------------------------------------------------------ plumbing
    def _budget_px(self) -> int:
        try:
            mb = int(setting("reader_cache_mb"))
        except Exception:
            mb = 256
        return max(max(16, mb) * 1024 * 1024 // 4, READER_MAX_PX)

    def _dpr(self) -> float:
        try:
            return max(1.0, float(self.devicePixelRatioF()))
        except Exception:
            return 1.0

    def has_worker(self) -> bool:
        return self._worker is not None

    def stop(self, wait_ms=8000) -> bool:
        """Cancel every renderer and wait for it. A QThread destroyed while it
        runs aborts the process, so this is called before the window closes."""
        self._req_timer.stop()
        ok = True
        for w in [self._worker] + list(self._retired):
            if w is None:
                continue
            w.cancel()
            ok = w.wait(wait_ms) and ok
        self._retired = [w for w in self._retired if w.isRunning()]
        return ok

    def is_loaded(self) -> bool:
        return self._sizes is not None

    def close_document(self, message="Opening…"):
        """Stop drawing and free the page pictures (the pages are not needed
        again until the next load)."""
        self._retire_worker()
        self._queue = None
        self._sizes = None
        self._pages = []
        self._lay = ReaderLayout([], 1.0)
        self._cache.clear()
        self._fallback.clear()
        self._failed.clear()
        self._hits.clear()
        self._sel.clear()
        self._message = message
        self._sync_scrollbars()
        self.viewport().update()

    def _retire_worker(self):
        w = self._worker
        self._worker = None
        if w is not None:
            w.cancel()
            _ALIVE_WORKERS.add(w)
            w.finished.connect(_reap_alive_workers)
            self._retired = [r for r in self._retired if r.isRunning()] + [w]

    # ------------------------------------------------------------ loading
    def load(self, path, pages, needle="", start=0):
        """Open `path` and show `pages` ([(source_idx, rotation)]), scrolled to
        display position `start` once the document has opened."""
        self._goto = None
        self._anchor_pos = None
        self._retire_worker()
        self._path = path
        self._queue = ReaderQueue()
        self._sizes = None
        self._pages = [tuple(p) for p in pages]
        self._cache = ReaderCache(self._budget_px())
        self._fallback.clear()
        self._failed.clear()
        self._sel.clear()
        self._hits.clear()
        self._cancel_edit()
        self._tsel = None
        self._tdrag = None
        self._texts = {}
        self._layouts.clear()
        self._layout_asked.clear()
        self._hover_seg = None
        self._pending_click = None
        self._fresh = None
        self._find_cursor = None
        self._needle = ""
        self._message = "Opening…"
        self._lay = ReaderLayout([], 1.0)
        self._sync_scrollbars()
        w = _ReaderWorker(path, self._queue)
        w.opened.connect(self._on_opened)
        w.rendered.connect(self._on_rendered)
        w.found.connect(self._on_found)
        w.laid.connect(self._on_laid)
        w.failed.connect(self._on_failed)
        self._worker = w
        _ALIVE_WORKERS.add(w)
        w.start()
        self._pending_needle = needle
        self._pending_start = int(start)
        self.viewport().update()

    def _on_opened(self, q, sizes):
        if q is not self._queue:
            return
        self._sizes = sizes
        if any(i >= len(sizes) for i, _r in self._pages):
            self._on_failed(q, "The PDF no longer matches the pages shown. Reload it.")
            return
        self._message = ""
        self._relayout(None)
        start, self._pending_start = getattr(self, "_pending_start", 0), 0
        if start:
            self.go_to(start)
        self.loaded.emit(len(self._pages))
        needle = getattr(self, "_pending_needle", "")
        self._pending_needle = ""
        if needle:
            self.set_find(needle, jump=False)

    def _on_failed(self, q, message):
        if q is not self._queue:
            return
        self._sizes = None
        self._message = message
        self._lay = ReaderLayout([], 1.0)
        self._sync_scrollbars()
        self.viewport().update()
        self.loadFailed.emit(message)

    # ------------------------------------------------------------ pages / zoom
    def pages(self):
        return list(self._pages)

    def count(self) -> int:
        return len(self._pages)

    def set_pages(self, pages):
        anchor = self._anchor()
        old_pos = anchor[3] if anchor else 0
        self._pages = [tuple(p) for p in pages]
        live = {i for i, _r in self._pages}
        self._sel &= live
        if self._sizes is None:
            return
        if anchor and anchor[0] not in live:           # the page we were on is gone: stay at the same place
            keep = min(old_pos, len(self._pages) - 1)
            anchor = (self._pages[keep][0], 0.0, None, keep) if self._pages else None
        self._relayout(anchor)
        if self._needle:
            self._queue_find()
        self.selectionChanged.emit()

    def zoom_mode(self):
        return self._mode

    def scale(self) -> float:
        return self._scale

    def set_zoom(self, mode):
        if mode not in (READER_FIT_WIDTH, READER_FIT_PAGE):
            mode = reader_clamp_zoom(mode)
        if mode == self._mode and self._lay.tops:
            return
        self._mode = mode
        self._relayout(self._anchor())

    def _anchor(self):
        if self._sizes is None or not len(self._lay):
            return None
        vh = self.viewport().height()
        sy = self.verticalScrollBar().value()
        pos = self._lay.current(sy, vh)
        frac = (sy + vh // 3 - self._lay.tops[pos]) / float(max(1, self._lay.heights[pos]))
        return (self._pages[pos][0], frac, None, pos)

    def _relayout(self, anchor):
        self._commit_editor()
        if self._sizes is None:
            return
        disp = [reader_display_size(*self._sizes[i], r) for i, r in self._pages]
        if self._mode in (READER_FIT_WIDTH, READER_FIT_PAGE) and disp:
            dw, dh = max(d[0] for d in disp), max(d[1] for d in disp)
            scale = reader_fit_scale(self._mode, dw, dh, 0,
                                     self.viewport().width(), self.viewport().height())
        else:
            scale = reader_clamp_zoom(self._mode if not isinstance(self._mode, str) else 1.0)
        changed = abs(scale - self._scale) > 1e-9
        self._scale = scale
        self._relaying = True
        try:
            self._lay = ReaderLayout(disp, scale)
            self._sync_scrollbars()
            vh = self.viewport().height()
            if anchor is not None and len(self._lay):
                pos = next((k for k, (i, _r) in enumerate(self._pages) if i == anchor[0]),
                           min(anchor[3], len(self._pages) - 1))
                probe = self._lay.tops[pos] + anchor[1] * self._lay.heights[pos]
                self.verticalScrollBar().setValue(int(max(0, probe - vh // 3)))
        finally:
            self._relaying = False
        if changed:
            self.scaleChanged.emit(scale)
        self.viewport().update()
        self._emit_page()
        self._req_timer.start()

    def _sync_scrollbars(self):
        vp = self.viewport()
        vsb, hsb = self.verticalScrollBar(), self.horizontalScrollBar()
        vsb.setRange(0, max(0, self._lay.total_height - vp.height()))
        vsb.setPageStep(max(1, vp.height() - 40))
        content_w = self._lay.max_width + 2 * self._lay.pad
        hsb.setRange(0, max(0, content_w - vp.width()))
        hsb.setPageStep(max(1, vp.width() - 40))

    # ------------------------------------------------------------ navigation
    def current_page(self) -> int:
        if not len(self._lay):
            return 0
        g = getattr(self, "_goto", None)
        v = self.verticalScrollBar().value()
        if g is not None and g[1] == v and 0 <= g[0] < len(self._lay):
            return g[0]                     # asked for this page; the scroll range clamped short of it
        return self._lay.current(v, self.viewport().height())

    def go_to(self, pos):
        if not len(self._lay):
            return
        pos = min(len(self._lay) - 1, max(0, int(pos)))
        vsb = self.verticalScrollBar()
        vsb.setValue(self._lay.scroll_for(pos))
        self._goto = (pos, vsb.value())
        self._emit_page()

    def _emit_page(self):
        p = self.current_page()
        if p != self._last_page:
            self._last_page = p
            self.setAccessibleDescription(f"Page {p + 1} of {len(self._pages)}" if self._pages else "")
            self.pageChanged.emit(p)

    def _on_scrolled(self, _v=0):
        self._place_editor()
        if self._relaying:
            return
        self.viewport().update()
        self._emit_page()
        self._req_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._sizes is None:
            return
        if self._mode in (READER_FIT_WIDTH, READER_FIT_PAGE):
            self._relayout(self._anchor())
        else:
            self._sync_scrollbars()
            self._req_timer.start()

    # ------------------------------------------------------------ rendering
    def _key(self, pos):
        idx, rot = self._pages[pos]
        w, h = self._sizes[idx]
        rs = reader_render_scale(w, h, self._scale, self._dpr())
        return ("r", idx, rot, int(round(rs * 1000))), rs

    def _request_visible(self):
        if self._sizes is None or not len(self._lay) or self._queue is None:
            return
        vh = self.viewport().height()
        sy = self.verticalScrollBar().value()
        first, last = self._lay.visible(sy, vh, prefetch=1)
        cur = self.current_page()
        keep = set()
        for pos in range(first, last + 1):
            key, rs = self._key(pos)
            keep.add(key)
            if key in self._cache or key in self._failed:
                continue
            self._queue.push(self._queue.generation, key, abs(pos - cur),
                             ("render", self._pages[pos][0], rs, self._pages[pos][1]))
        keep |= self._find_keys()
        keep |= self._layout_keys()
        self._queue.prune(keep)
        if self._tool_text:
            self._ask_visible_layouts()
        near = {self._pages[p][0] for p in range(max(0, first - 1), min(len(self._pages), last + 2))}
        for k in [k for k in self._fallback if k not in near]:
            del self._fallback[k]
        budget = self._budget_px()                     # the stand-in pictures count against the memory budget too
        used = sum(im.width() * im.height() for _r, im in self._fallback.values())
        if used > budget:
            at = {self._pages[p][0]: p for p in range(len(self._pages))}
            for k in sorted(self._fallback, key=lambda i: -abs(at.get(i, 0) - cur)):
                if used <= budget:
                    break
                im = self._fallback.pop(k)[1]
                used -= im.width() * im.height()

    def _on_rendered(self, q, key, img):
        if q is not self._queue:
            return
        if img is None:
            self._failed.add(key)
        else:
            self._cache.put(key, img, img.width() * img.height())
            self._fallback[key[1]] = (key[2], img)
        self.viewport().update()

    # ------------------------------------------------------------ painting
    def _page_x(self, w, vw) -> int:
        content_w = max(self._lay.max_width + 2 * self._lay.pad, vw)
        return (content_w - w) // 2 - self.horizontalScrollBar().value()

    def paintEvent(self, event):
        p = QPainter(self.viewport())
        p.fillRect(event.rect(), QColor(theme_color("grid")))
        if self._sizes is None or not len(self._lay):
            p.setPen(QColor(theme_color("text")))
            p.drawText(self.viewport().rect(), Qt.AlignCenter | Qt.TextWordWrap,
                       self._message or "This PDF has no pages to show.")
            p.end()
            return
        vw, vh = self.viewport().width(), self.viewport().height()
        sy = self.verticalScrollBar().value()
        first, last = self._lay.visible(sy, vh, prefetch=0)
        border = QColor(theme_color("border"))
        accent = QColor(theme_color("accent"))
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        for pos in range(first, last + 1):
            idx, rot = self._pages[pos]
            w, h = self._lay.widths[pos], self._lay.heights[pos]
            x, y = self._page_x(w, vw), self._lay.tops[pos] - sy
            rect = QRect(x, y, w, h)
            p.fillRect(rect.adjusted(3, 3, 3, 3), QColor(0, 0, 0, 95))
            p.fillRect(rect, QColor("#ffffff"))
            key, _rs = self._key(pos)
            img = self._cache.get(key)
            if img is None:
                fb = self._fallback.get(idx)
                if fb is not None and fb[0] == rot:
                    img = fb[1]
            if img is not None:
                p.drawImage(rect, img)
            else:
                p.setPen(QColor("#555555"))
                p.drawText(rect, Qt.AlignCenter,
                           "This page could not be drawn." if key in self._failed else "Drawing…")
            if self._texts.get(idx):
                self._paint_texts(p, idx, rot, x, y)
                self._paint_text_frames(p, pos, idx, rot)
            if self._hover_seg is not None and self._hover_seg[0] == idx and self._tool_text and not rot:
                self._paint_seg_hover(p, idx, rect)
            for k, (fx0, fy0, fx1, fy1) in enumerate(self._hits.get((idx, rot), ())):
                hr = QRect(x + int(fx0 * w), y + int(fy0 * h),
                           max(3, int((fx1 - fx0) * w)), max(3, int((fy1 - fy0) * h)))
                cur = self._find_cursor == (idx, k)
                p.setCompositionMode(QPainter.CompositionMode_Multiply)     # tints the page, the text stays black
                p.fillRect(hr, QColor(255, 150, 40) if cur else QColor(255, 235, 60))
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)
                if cur:
                    pen = QPen(QColor(255, 120, 0))
                    pen.setWidth(2)
                    p.setPen(pen)
                    p.drawRect(hr)
            pen = QPen(accent if idx in self._sel else border)
            pen.setWidth(4 if idx in self._sel else 1)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(rect.adjusted(-1, -1, 0, 0) if idx not in self._sel else rect.adjusted(-2, -2, 2, 2))
        if self._region_drag is not None:
            d = self._region_drag
            band = QRect(d["a"], d["b"]).normalized()
            accent2 = QColor(theme_color("accent"))
            p.fillRect(band, QColor(accent2.red(), accent2.green(), accent2.blue(), 50))
            pen = QPen(accent2)
            pen.setWidth(2)
            p.setPen(pen)
            p.drawRect(band)
        if self.hasFocus():                           # a visible focus ring for keyboard users
            ring = QPen(accent)
            ring.setWidth(2)
            p.setPen(ring)
            p.drawRect(self.viewport().rect().adjusted(1, 1, -1, -1))
        p.end()

    # ------------------------------------------------------------ selection
    def _pos_at(self, point):
        if not len(self._lay):
            return None
        sy = self.verticalScrollBar().value()
        y = point.y() + sy
        vw = self.viewport().width()
        first, last = self._lay.visible(sy, self.viewport().height(), prefetch=0)
        for pos in range(first, last + 1):
            top, h, w = self._lay.tops[pos], self._lay.heights[pos], self._lay.widths[pos]
            x = self._page_x(w, vw)
            if top <= y < top + h and x <= point.x() < x + w:
                return pos
        return None

    def selection(self):
        return sorted(pos for pos, (i, _r) in enumerate(self._pages) if i in self._sel)

    def select_positions(self, positions, notify=True):
        self._sel = {self._pages[p][0] for p in positions if 0 <= p < len(self._pages)}
        self.viewport().update()
        if notify:
            self.selectionChanged.emit()

    def mousePressEvent(self, event):
        self.setFocus()
        self._panned = False
        self._press = None
        if self._region_armed and event.button() == Qt.LeftButton:
            self._region_press(event)
            return
        if self._tool_text and event.button() == Qt.LeftButton and not (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)) \
                and self._text_press(event):
            return
        if event.button() not in (Qt.LeftButton, Qt.RightButton):
            return super().mousePressEvent(event)         # middle / back / forward never touch the selection
        if event.button() == Qt.LeftButton and not (event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)):
            # A plain click selects on RELEASE; a drag pans the page like a hand tool.
            self._press = (event.position().toPoint(), self.horizontalScrollBar().value(),
                           self.verticalScrollBar().value())
            return
        self._apply_click(event.button(), event.position().toPoint(), event.modifiers())

    def mouseMoveEvent(self, event):
        if self._region_drag is not None and event.buttons() & Qt.LeftButton:
            self._region_move(event.position().toPoint())
            return
        if self._tdrag is not None and event.buttons() & Qt.LeftButton:
            self._drag_to(event.position().toPoint())
            return
        if self._tool_text and not event.buttons():
            self._text_hover(event.position().toPoint())
        pr = getattr(self, "_press", None)
        if pr is not None and event.buttons() & Qt.LeftButton:
            d = event.position().toPoint() - pr[0]
            if self._panned or d.manhattanLength() > QApplication.startDragDistance():
                if not self._panned:
                    self._panned = True
                    self.viewport().setCursor(Qt.ClosedHandCursor)
                self.horizontalScrollBar().setValue(pr[1] - d.x())
                self.verticalScrollBar().setValue(pr[2] - d.y())
            return
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._tool_text and event.button() == Qt.LeftButton:
            hit = self._hit_text(event.position().toPoint())
            if hit is not None:
                self._select_box(hit[1], hit[2])
                self._begin_edit(hit[1], hit[2])
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        if self._region_armed and event.button() == Qt.LeftButton:
            self._region_release()
            return
        if self._tdrag is not None and event.button() == Qt.LeftButton:
            self._finish_drag()
            return
        pr = getattr(self, "_press", None)
        if pr is not None and event.button() == Qt.LeftButton:
            self._press = None
            self.viewport().setCursor(self._idle_cursor())
            if not self._panned and self.viewport().rect().contains(event.position().toPoint()):
                self._apply_click(Qt.LeftButton, event.position().toPoint(), event.modifiers())
            self._panned = False
            return
        super().mouseReleaseEvent(event)

    def _apply_click(self, button, point, mods):
        if self._tool_text and button == Qt.LeftButton and not (mods & (Qt.ControlModifier | Qt.ShiftModifier)) \
                and self._text_click(point, mods):
            return
        pos = self._pos_at(point)
        if pos is None:
            if button == Qt.LeftButton and not (mods & (Qt.ControlModifier | Qt.ShiftModifier)):
                self.select_positions([])
            return
        idx = self._pages[pos][0]
        if button == Qt.RightButton and idx in self._sel:
            return                                        # keep a multi-selection for the menu
        if mods & Qt.ShiftModifier and self._anchor_pos is not None:
            a, b = sorted((self._anchor_pos, pos))
            self.select_positions(range(a, b + 1))
        elif mods & Qt.ControlModifier:
            self._anchor_pos = pos
            self.select_positions(sorted(set(self.selection()) ^ {pos}))
        else:
            self._anchor_pos = pos
            self.select_positions([pos])

    def _on_context(self, local):
        self._ctx = None
        hit = self._hit_text(local)
        if hit is not None:
            self._ctx = ("box", hit[1], hit[2])
            self._tsel = (hit[1], hit[2])
            self.viewport().update()
            self.textStyleChanged.emit()
        else:
            pos = self._pos_at(local)
            if pos is not None:
                if self._tool_text and not self._pages[pos][1]:
                    self.request_layout(self._pages[pos][0])
                seg = self._hit_seg(local) if self._tool_text else None
                self._ctx = ("seg", pos, QPoint(local), seg[1], seg[2]) if seg else ("page", pos, QPoint(local))
        self.contextRequested.emit(self.viewport().mapToGlobal(local))

    def context_target(self):
        """What the last right-click landed on: ('box', idx, k), ('page', pos, point) or None."""
        return getattr(self, "_ctx", None)

    def event(self, e):
        # Ctrl+1 / Ctrl+2 are also the window's tab-switch keys; while the reader has focus they are zoom keys.
        if e.type() == QEvent.ShortcutOverride and e.modifiers() == Qt.ControlModifier \
                and e.key() in (Qt.Key_1, Qt.Key_2):
            e.accept()
            return True
        return super().event(e)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.viewport().update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.viewport().update()

    def keyPressEvent(self, event):
        key, mods = event.key(), event.modifiers()
        if self._tool_text and self._edit is None and self._text_key(event):
            event.accept()
            return
        if key == Qt.Key_T and not mods:
            self.textToolKey.emit()
        elif mods & Qt.ControlModifier and key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoomKey.emit(1)
        elif mods & Qt.ControlModifier and key == Qt.Key_Minus:
            self.zoomKey.emit(-1)
        elif mods & Qt.ControlModifier and key == Qt.Key_0:
            self.zoomKey.emit(0)
        elif mods & Qt.ControlModifier and key == Qt.Key_1:
            self.zoomKey.emit(100)
        elif mods & Qt.ControlModifier and key == Qt.Key_2:
            self.zoomKey.emit(101)
        elif mods & Qt.ControlModifier and key == Qt.Key_G:
            self.goToKey.emit()
        elif mods & Qt.ControlModifier and key == Qt.Key_F:
            self.findKey.emit()
        elif mods & Qt.ControlModifier and mods & Qt.ShiftModifier and key in (Qt.Key_R, Qt.Key_L):
            self.rotateKey.emit(90 if key == Qt.Key_R else -90)
        elif key in (Qt.Key_Return, Qt.Key_Enter) and not mods & (Qt.ControlModifier | Qt.AltModifier):
            cur = self.current_page()                 # keyboard selection: Enter toggles the page on screen
            self._anchor_pos = cur
            self.select_positions(sorted(set(self.selection()) ^ {cur}))
        elif mods & Qt.ShiftModifier and key in (Qt.Key_Up, Qt.Key_Down) and self._pages:
            a = self._anchor_pos if self._anchor_pos is not None else self.current_page()
            sel = self.selection()
            end = (max(sel) if sel and max(sel) > a else min(sel) if sel else a) + (1 if key == Qt.Key_Down else -1)
            end = min(len(self._pages) - 1, max(0, end))
            self._anchor_pos = a
            self.select_positions(range(min(a, end), max(a, end) + 1))
            self.go_to(end)
        elif key in (Qt.Key_Plus, Qt.Key_Equal) and not mods & Qt.ControlModifier:
            self.zoomKey.emit(1)
        elif key == Qt.Key_Minus and not mods & Qt.ControlModifier:
            self.zoomKey.emit(-1)
        elif mods & Qt.ControlModifier and key == Qt.Key_A:
            self.select_positions(range(len(self._pages)))
        elif key == Qt.Key_Delete and self._sel:
            self.deleteRequested.emit()
        elif key == Qt.Key_Space:
            sb = self.verticalScrollBar()
            sb.setValue(sb.value() + (-1 if mods & Qt.ShiftModifier else 1) * sb.pageStep())
        elif key == Qt.Key_Home:
            self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
        elif key == Qt.Key_End:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        elif key in (Qt.Key_PageUp, Qt.Key_PageDown):
            sb = self.verticalScrollBar()
            sb.setValue(sb.value() + (-1 if key == Qt.Key_PageUp else 1) * sb.pageStep())
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            d = event.angleDelta().y()
            if d:
                self.zoomKey.emit(1 if d > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------ floating text
    # Boxes live in `_texts` (source page index -> tuple of TextBox). They are drawn from the page's own
    # unrotated frame, so an Organizer rotation turns them with the page. They can be edited only while
    # the Text tool is on and the page is upright. Every finished gesture (create, type, move, resize,
    # style, delete) is reported once through textsChanged; the Organizer owns undo and saving.
    def set_text_tool(self, on):
        on = bool(on)
        if not on:
            self._commit_editor()
            self._tsel = None
            self._region_armed = False
            self._region_drag = None
        self._tool_text = on
        if on:
            self._ask_visible_layouts()                   # the page's own words, so a click on them can edit them
        self.viewport().setMouseTracking(on)
        self.viewport().setCursor(self._idle_cursor())
        self.viewport().update()
        self.textStyleChanged.emit()

    def text_tool_on(self) -> bool:
        return self._tool_text

    def _idle_cursor(self):
        return Qt.IBeamCursor if self._tool_text else Qt.OpenHandCursor

    def set_texts(self, texts):
        self._cancel_edit()
        self._texts = {int(i): tuple(b) for i, b in (texts or {}).items() if b}
        if self._tsel is not None and (self._tsel[0] not in self._texts
                                       or self._tsel[1] >= len(self._texts[self._tsel[0]])):
            self._tsel = None
        self.viewport().update()
        self.textStyleChanged.emit()

    def texts(self):
        return dict(self._texts)

    def selected_text(self):
        """(source page index, box number) of the selected box, or None."""
        return self._tsel

    def text_style(self) -> dict:
        """The style the next new box gets, or the selected box's own."""
        sel = self._selected_box()
        src = sel if sel is not None else TextBox(**self._text_style)
        return {k: getattr(src, k) for k in _TEXT_STYLE_KEYS}

    def set_text_style(self, **kw):
        kw = {k: v for k, v in kw.items() if k in _TEXT_STYLE_KEYS}
        if not kw:
            return
        self._text_style.update(kw)
        self._text_style = {k: getattr(clean_text_box(self._text_style), k) for k in _TEXT_STYLE_KEYS}
        if self._tsel is None:
            self.textStyleChanged.emit()
            return
        self._pull_editor_text()
        idx, k = self._tsel
        boxes = list(self._texts.get(idx, ()))
        if k >= len(boxes):
            return
        box = clean_text_box(replace(boxes[k], **self._text_style))
        boxes[k] = self._autosize(idx, box)
        self._texts[idx] = tuple(boxes)
        if self._edit is not None:
            self._restyle_editor()
        else:
            self._emit_texts(idx)
        self.viewport().update()

    def set_text_style_kw(self, key, value):
        self.set_text_style(**{key: value})

    def toggle_text_style(self, name):
        cur = self.text_style().get(name)
        self.set_text_style(**{name: not cur})

    def delete_selected_text(self) -> bool:
        if self._tsel is None:
            return False
        idx, k = self._tsel
        self._cancel_edit()
        boxes = list(self._texts.get(idx, ()))
        if k >= len(boxes):
            self._tsel = None
            return False
        del boxes[k]
        self._set_boxes(idx, boxes)
        self._tsel = None
        self._emit_texts(idx)
        self.textStyleChanged.emit()
        return True

    def duplicate_selected_text(self) -> bool:
        if self._tsel is None:
            return False
        idx, k = self._tsel
        boxes = self._texts.get(idx, ())
        if k >= len(boxes) or len(boxes) >= TEXT_BOXES_PER_PAGE_MAX:
            return False
        self._cancel_edit()
        box = boxes[k]
        bw, bh = self._sizes[idx]
        nb = self._autosize(idx, replace(box, x=min(box.x + 10 / bw, 1.0 - box.w), y=min(box.y + 10 / bh, 1.0 - box.h),
                                         erase=(), orig=""))              # a copy adds words; it does not take the old ones out again
        lst = list(boxes) + [nb]
        self._set_boxes(idx, lst)
        self._tsel = (idx, len(lst) - 1)
        self._emit_texts(idx)
        self.textStyleChanged.emit()
        return True

    def copy_selected_text(self) -> bool:
        """Put the selected box on the clipboard: its words as plain text (so other programs get them) and the whole box
        (so pasting here brings the look back too)."""
        box = self._selected_box()
        if box is None:
            return False
        import json
        from PySide6.QtCore import QMimeData
        data = {k: getattr(box, k) for k in ("w", "text", "family", "size", "bold", "italic", "underline", "color", "align", "spacing")}
        md = QMimeData()
        md.setText(box.text)
        md.setData(_TEXT_MIME, QByteArray(json.dumps(data).encode("utf-8")))
        QApplication.clipboard().setMimeData(md)
        return True

    def cut_selected_text(self) -> bool:
        box = self._selected_box()
        if box is None or box.erase:                       # taking the old words back out is "put the original text back", not cut
            return False
        return self.copy_selected_text() and self.delete_selected_text()

    def can_paste_text(self) -> bool:
        md = QApplication.clipboard().mimeData()
        return bool(md is not None and (md.hasFormat(_TEXT_MIME) or (md.hasText() and md.text().strip())))

    def paste_text(self, pos=None, point=None) -> bool:
        """Paste a copied box (or plain text) onto the page at `pos`: at `point` when given, else near the top of the page as
        shown, or beside the selected box."""
        md = QApplication.clipboard().mimeData()
        if md is None or not self.can_paste_text() or self._sizes is None or not len(self._lay):
            return False
        if pos is None:
            pos = self._pos_for(self._tsel[0]) if self._tsel is not None else self.current_page()
        if pos is None or not 0 <= pos < len(self._pages):
            return False
        idx, rot = self._pages[pos]
        if rot:
            self.textHint.emit("Turn this page back upright to paste text onto it.")
            return True
        boxes = list(self._texts.get(idx, ()))
        if len(boxes) >= TEXT_BOXES_PER_PAGE_MAX:
            self.textHint.emit(f"A page can hold {TEXT_BOXES_PER_PAGE_MAX} text boxes.")
            return True
        import json
        data = None
        if md.hasFormat(_TEXT_MIME):
            try:
                data = json.loads(bytes(md.data(_TEXT_MIME)).decode("utf-8"))
                data = data if isinstance(data, dict) else None
            except (ValueError, UnicodeDecodeError):
                data = None
        if data is None:
            data = dict(self._text_style, text=md.text()[:TEXT_BOX_MAX_CHARS], w=0.4)
        base = clean_text_box(dict(data, x=0.1, y=0.1, h=0.02, erase=(), orig=""))
        r = self._page_rect(pos)
        sel = self._selected_box() if self._tsel and self._tsel[0] == idx else None
        if point is not None and r.width() > 0 and r.height() > 0:
            x, y = (point.x() - r.x()) / r.width(), (point.y() - r.y()) / r.height()
        elif sel is not None:
            bw, bh = self._sizes[idx]
            x, y = sel.x + 12 / bw, sel.y + 12 / bh
        else:
            top = max(0.0, (self.verticalScrollBar().value() - self._lay.tops[pos]) / float(max(1, self._lay.heights[pos])))
            x, y = 0.1, min(0.9, top + 0.06)
        box = self._autosize(idx, replace(base, x=min(max(0.0, x), 1.0 - base.w), y=min(max(0.0, y), 0.95)))
        boxes.append(box)
        self._set_boxes(idx, boxes)
        self._select_box(idx, len(boxes) - 1)
        self._emit_texts(idx)
        return True

    def reorder_selected_text(self, front: bool) -> bool:
        """Bring the selected box in front of the others on its page, or send it behind them."""
        if self._tsel is None:
            return False
        idx, k = self._tsel
        lst = list(self._texts.get(idx, ()))
        if k >= len(lst) or len(lst) < 2:
            return False
        self._cancel_edit()
        box = lst.pop(k)
        nk = len(lst) if front else 0
        if nk == k:
            lst.insert(k, box)
            return False
        lst.insert(nk, box)
        self._set_boxes(idx, lst)
        self._tsel = (idx, nk)
        self._emit_texts(idx)
        self.textStyleChanged.emit()
        return True

    def edit_selected_text(self) -> bool:
        if self._tsel is None:
            return False
        self._begin_edit(*self._tsel)
        return self._edit is not None

    def text_escape(self) -> bool:
        if self._region_armed:
            self._disarm_region()
            return True
        if self._edit is not None:
            self._commit_editor()
            self.setFocus()
            return True
        if self._tsel is not None:
            self._tsel = None
            self.viewport().update()
            self.textStyleChanged.emit()
            return True
        return False

    # -- geometry
    def _pos_for(self, idx):
        return next((k for k, (i, _r) in enumerate(self._pages) if i == idx), None)

    def _page_rect(self, pos):
        w, h = self._lay.widths[pos], self._lay.heights[pos]
        return QRect(self._page_x(w, self.viewport().width()),
                     self._lay.tops[pos] - self.verticalScrollBar().value(), w, h)

    def _box_rect(self, pos, box):
        r = self._page_rect(pos)
        return QRect(r.x() + int(box.x * r.width()), r.y() + int(box.y * r.height()),
                     max(8, int(box.w * r.width())), max(8, int(box.h * r.height())))

    def _selected_box(self):
        if self._tsel is None:
            return None
        idx, k = self._tsel
        boxes = self._texts.get(idx, ())
        return boxes[k] if 0 <= k < len(boxes) else None

    def _autosize(self, idx, box):
        """The box as tall as its text, and still on the page."""
        bw, bh = self._sizes[idx]
        h = text_box_height_frac(box, bw, bh)
        return replace(box, h=h, y=max(0.0, min(box.y, 1.0 - h)))

    def _set_boxes(self, idx, boxes):
        if boxes:
            self._texts[idx] = tuple(boxes)
        else:
            self._texts.pop(idx, None)
        self.viewport().update()

    def _emit_texts(self, idx):
        self.textsChanged.emit(idx, tuple(self._texts.get(idx, ())))

    def _hit_text(self, point):
        """(pos, idx, k, kind) for the box under `point` (kind 'W'/'E' resize handle or 'body'), or None."""
        if not len(self._lay):
            return None
        first, last = self._lay.visible(self.verticalScrollBar().value(), self.viewport().height(), prefetch=0)
        for pos in range(first, last + 1):
            idx, rot = self._pages[pos]
            boxes = self._texts.get(idx)
            if rot or not boxes:
                continue
            for k in range(len(boxes) - 1, -1, -1):
                r = self._box_rect(pos, boxes[k])
                if self._tsel == (idx, k):
                    for kind, hx in (("W", r.left() - 13), ("E", r.right() - 2)):      # just outside the sides, so they never sit on the words
                        if QRect(hx, r.center().y() - 9, 16, 18).contains(point):
                            return pos, idx, k, kind
                if r.adjusted(-3, -3, 3, 3).contains(point):
                    return pos, idx, k, "body"
        return None

    # -- painting
    def _paint_texts(self, p, idx, rot, x, y):
        bw, bh = self._sizes[idx]
        s = self._scale
        t = QTransform.fromScale(s / _TEXT_UNITS, s / _TEXT_UNITS)
        t = t * text_rot_transform(rot, bw * s, bh * s) * QTransform.fromTranslate(x, y)
        p.save()
        p.setTransform(t)
        for k, box in enumerate(self._texts.get(idx, ())):
            if self._edit != (idx, k):
                draw_text_box(p, box, bw, bh)
            else:
                draw_text_cover(p, box, bw, bh)           # the old words stay hidden while their replacement is typed
        p.restore()

    def _paint_seg_hover(self, p, idx, page_rect):
        runs = self._layouts.get(idx) or ()
        i = self._hover_seg[1]
        if not 0 <= i < len(runs):
            return
        run = runs[i]
        accent = QColor(theme_color("accent"))
        r = QRect(page_rect.x() + int(run["x0"] * page_rect.width()) - 2, page_rect.y() + int(run["y0"] * page_rect.height()) - 2,
                  int((run["x1"] - run["x0"]) * page_rect.width()) + 4, int((run["y1"] - run["y0"]) * page_rect.height()) + 4)
        p.fillRect(r, QColor(accent.red(), accent.green(), accent.blue(), 45))
        pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), 170))
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)

    def _paint_text_frames(self, p, pos, idx, rot):
        if not self._tool_text or rot:
            return
        boxes = self._texts.get(idx, ())
        accent = QColor(theme_color("accent"))
        p.save()                                               # the pen and brush set here must not leak into the page border
        for k, box in enumerate(boxes):
            r = self._box_rect(pos, box)
            sel = self._tsel == (idx, k)
            pen = QPen(accent if sel else QColor(accent.red(), accent.green(), accent.blue(), 110))
            pen.setStyle(Qt.SolidLine if sel else Qt.DashLine)
            pen.setWidth(2 if sel else 1)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(-1, -1, 1, 1))              # just outside the box, so its line never covers a descender
            if sel and self._edit is None:
                p.setBrush(accent)
                for hx in (r.left() - 11, r.right() + 2):                  # outside the box: they do not cover the first or last letter
                    p.drawRoundedRect(QRect(hx, r.center().y() - 8, 9, 16), 3, 3)
        p.restore()

    # -- mouse
    def _text_press(self, event) -> bool:
        hit = self._hit_text(event.position().toPoint())
        if self._edit is not None:
            self._commit_editor()
        if hit is None:
            return False
        pos, idx, k, kind = hit
        self._select_box(idx, k)
        self._tdrag = {"pos": pos, "idx": idx, "k": k, "kind": kind, "start": event.position().toPoint(),
                       "orig": self._texts[idx][k], "moved": False}
        self.viewport().setCursor(Qt.SizeAllCursor if kind == "body" else Qt.SizeHorCursor)
        return True

    def _drag_to(self, point):
        d = self._tdrag
        r = self._page_rect(d["pos"])
        if r.width() < 1 or r.height() < 1:
            return
        dx = (point.x() - d["start"].x()) / r.width()
        dy = (point.y() - d["start"].y()) / r.height()
        if not d["moved"] and abs(point.x() - d["start"].x()) + abs(point.y() - d["start"].y()) < 3:
            return
        d["moved"] = True
        o = d["orig"]
        if d["kind"] == "body":
            nb = replace(o, x=min(max(0.0, o.x + dx), 1.0 - o.w), y=min(max(0.0, o.y + dy), 1.0 - o.h))
        elif d["kind"] == "E":
            nb = replace(o, w=min(max(TEXT_MIN_W, o.w + dx), 1.0 - o.x))
        else:
            right = o.x + o.w
            nx = min(max(0.0, o.x + dx), right - TEXT_MIN_W)
            nb = replace(o, x=nx, w=right - nx)
        boxes = list(self._texts[d["idx"]])
        boxes[d["k"]] = self._autosize(d["idx"], nb)
        self._texts[d["idx"]] = tuple(boxes)
        self.viewport().update()

    def _finish_drag(self):
        d, self._tdrag = self._tdrag, None
        self.viewport().setCursor(self._idle_cursor())
        if d and d["moved"]:
            self._emit_texts(d["idx"])
        self.viewport().update()

    def _text_hover(self, point):
        hit = self._hit_text(point)
        seg = None
        if hit is None:
            self.viewport().setCursor(Qt.IBeamCursor)
            seg = self._hit_seg(point)
        else:
            self.viewport().setCursor(Qt.SizeAllCursor if hit[3] == "body" else Qt.SizeHorCursor)
        new = (seg[1], seg[2]) if seg else None
        if new != self._hover_seg:
            self._hover_seg = new
            self.viewport().update()
            if new is not None:
                self.textHint.emit("Click to edit this line. Alt+click edits the whole paragraph. Right-click for more.")

    def _text_click(self, point, mods=Qt.NoModifier) -> bool:
        """A click (no drag) with the Text tool on: page text under it is made editable (Alt: its whole paragraph),
        empty page makes a new box."""
        pos = self._pos_at(point)
        if pos is None:
            self._select_box(None)
            return True
        idx, rot = self._pages[pos]
        if not rot:
            if idx not in self._layouts:
                self.request_layout(idx)
                if idx in self._layout_asked:            # its words are still being read: answer the click when they are in
                    self._pending_click = (idx, QPoint(point), mods)
                    QTimer.singleShot(1500, _weak(self._pending_click_timeout))
                    return True
            hit = self._hit_seg(point)
            if hit is not None and not mods & Qt.ControlModifier:
                return self.edit_existing_text(hit[1], hit[2], paragraph=bool(mods & Qt.AltModifier))
        return self.new_box_at(pos, point)

    def _pending_click_timeout(self):
        pend, self._pending_click = self._pending_click, None
        if pend is not None:
            self._layout_asked.discard(pend[0])
            self._layouts.setdefault(pend[0], [])
            self._text_click(pend[1], pend[2])

    def new_box_at(self, pos, point) -> bool:
        """Start an empty box on the page at `pos`, its top-left near `point` (viewport coordinates)."""
        idx, rot = self._pages[pos]
        if rot:
            self.textHint.emit("Turn this page back upright to add text. Text on a turned page moves with it.")
            return True
        r = self._page_rect(pos)
        bw, bh = self._sizes[idx]
        base = clean_text_box(dict(self._text_style, x=0, y=0, w=0.4, h=0.02))
        w = min(base.w, 0.96)
        x = min(max(0.0, (point.x() - r.x()) / r.width()), 1.0 - w)
        box = replace(base, x=x, y=max(0.0, (point.y() - r.y()) / r.height() - base.size * 0.5 / bh), w=w)
        box = self._autosize(idx, replace(box, text=" "))
        box = replace(box, text="")
        boxes = list(self._texts.get(idx, ()))
        if len(boxes) >= TEXT_BOXES_PER_PAGE_MAX:
            self.textHint.emit(f"A page can hold {TEXT_BOXES_PER_PAGE_MAX} text boxes.")
            return True
        boxes.append(box)
        self._set_boxes(idx, boxes)
        self._select_box(idx, len(boxes) - 1)
        self._begin_edit(idx, len(boxes) - 1)
        return True

    def _select_box(self, idx, k=None):
        self._tsel = None if idx is None else (idx, k)
        self.viewport().update()
        self.textStyleChanged.emit()

    # -- text that is already on the page
    # A page's own words are read (in the worker, once per page, only while the Text tool is on) as runs: one run is
    # the text on one line. Clicking a run makes a box that sits exactly on those words; typing replaces them. The old
    # words are taken out of the page (or, where that cannot be checked, covered) when the file is saved.
    def _layout_keys(self):
        return {("t", i) for i in self._layout_asked if i not in self._layouts}

    def request_layout(self, idx):
        if idx in self._layouts or idx in self._layout_asked or self._queue is None or self._sizes is None:
            return
        self._layout_asked.add(idx)
        self._queue.push(self._queue.generation, ("t", idx), 0, ("layout", idx))

    def _ask_visible_layouts(self):
        if not len(self._lay):
            return
        first, last = self._lay.visible(self.verticalScrollBar().value(), self.viewport().height(), prefetch=0)
        for pos in range(first, min(last, first + 6) + 1):
            if not self._pages[pos][1]:
                self.request_layout(self._pages[pos][0])

    def _on_laid(self, q, key, runs):
        if q is not self._queue:
            return
        idx = key[1]
        self._layout_asked.discard(idx)
        self._layouts[idx] = list(runs or ())
        while len(self._layouts) > 30:
            self._layouts.pop(next(iter(self._layouts)))
        pend = self._pending_click
        if pend is not None and pend[0] == idx:
            self._pending_click = None
            self._text_click(pend[1], pend[2])
        self.viewport().update()

    def _covered_run(self, idx, run) -> bool:
        """True when an edit box already took this run's words (clicking it again would edit them twice)."""
        cx, cy = (run["x0"] + run["x1"]) / 2.0, (run["y0"] + run["y1"]) / 2.0
        for b in self._texts.get(idx, ()):
            for (a, c, d, e) in b.erase:
                if a <= cx <= d and c <= cy <= e:
                    return True
        return False

    def _hit_seg(self, point):
        """(pos, idx, run number) of the page text under `point`, or None. Needs the page's text to have been read."""
        if not len(self._lay) or self._sizes is None:
            return None
        first, last = self._lay.visible(self.verticalScrollBar().value(), self.viewport().height(), prefetch=0)
        for pos in range(first, last + 1):
            idx, rot = self._pages[pos]
            runs = self._layouts.get(idx)
            if rot or not runs:
                continue
            r = self._page_rect(pos)
            if not r.adjusted(-2, -2, 2, 2).contains(point):
                continue
            best = None
            for i, run in enumerate(runs):
                if not run["edit"]:
                    continue
                rr = QRect(r.x() + int(run["x0"] * r.width()), r.y() + int(run["y0"] * r.height()),
                           int((run["x1"] - run["x0"]) * r.width()), int((run["y1"] - run["y0"]) * r.height()))
                if rr.adjusted(-2, -2, 2, 2).contains(point) and not self._covered_run(idx, run):
                    area = max(1, rr.width() * rr.height())
                    if best is None or area < best[0]:
                        best = (area, i)
            if best is not None:
                return pos, idx, best[1]
        return None

    def _sample_cover(self, idx, rects) -> str:
        """The colour of the page just outside the old words, read off the picture of the page: what a patch should be
        so it disappears into the paper."""
        fb = self._fallback.get(idx)
        img = fb[1] if fb is not None and not fb[0] else None
        if img is None or img.width() < 4 or img.height() < 4:
            return "#ffffff"
        W, H = img.width(), img.height()
        rs, gs, bs = [], [], []

        def take(x, y):
            if 0 <= x < W and 0 <= y < H:
                c = img.pixelColor(x, y)
                rs.append(c.red())
                gs.append(c.green())
                bs.append(c.blue())
        for (a, b, c, d) in rects:
            x0, y0, x1, y1 = int(a * W), int(b * H), int(c * W), int(d * H)
            step = max(1, (x1 - x0) // 24)
            for x in range(x0, x1 + 1, step):
                take(x, y0 - 2)
                take(x, y1 + 2)
            step = max(1, (y1 - y0) // 8)
            for y in range(y0, y1 + 1, step):
                take(x0 - 2, y)
                take(x1 + 2, y)
        if not rs:
            return "#ffffff"
        rs.sort()
        gs.sort()
        bs.sort()
        m = len(rs) // 2
        return "#%02x%02x%02x" % (rs[m], gs[m], bs[m])

    def edit_existing_text(self, idx, run_no, paragraph=False) -> bool:
        """Make the words of run `run_no` on page `idx` (or its whole paragraph) editable in place."""
        runs = self._layouts.get(idx) or []
        pos = self._pos_for(idx)
        if pos is None or not 0 <= run_no < len(runs) or self._pages[pos][1] or not runs[run_no]["edit"]:
            return False
        boxes = list(self._texts.get(idx, ()))
        if len(boxes) >= TEXT_BOXES_PER_PAGE_MAX:
            self.textHint.emit(f"A page can hold {TEXT_BOXES_PER_PAGE_MAX} text boxes.")
            return True
        bw, bh = self._sizes[idx]
        ids = text_paragraph(runs, run_no, bw, bh) if paragraph else [run_no]
        ids = [j for j in ids if not self._covered_run(idx, runs[j])] or [run_no]
        rects = [(runs[j]["x0"], runs[j]["y0"], runs[j]["x1"], runs[j]["y1"]) for j in ids]
        box = text_box_from_segments(runs, ids, bw, bh, self._sample_cover(idx, rects))
        boxes.append(box)
        self._set_boxes(idx, boxes)
        k = len(boxes) - 1
        self._select_box(idx, k)
        self._begin_edit(idx, k)
        self._fresh = (idx, k, box)
        self._hover_seg = None
        return True

    def arm_region(self) -> bool:
        """For a scan (or anything whose words are a picture): the next drag marks the area to cover, and what is typed
        goes in its place. The picture is hidden under a patch the colour of the paper."""
        if self._sizes is None or not len(self._lay):
            return False
        self._commit_editor()
        self._region_armed = True
        self._region_drag = None
        self.viewport().setCursor(Qt.CrossCursor)
        self.textHint.emit("Drag around the words to replace, then type. Esc cancels.")
        self.viewport().update()
        return True

    def _disarm_region(self):
        self._region_armed = False
        self._region_drag = None
        self.viewport().setCursor(self._idle_cursor())
        self.viewport().update()

    def _region_press(self, event):
        point = event.position().toPoint()
        pos = self._pos_at(point)
        if pos is None:
            return
        if self._pages[pos][1]:
            self.textHint.emit("Turn this page back upright to replace text on it.")
            self._disarm_region()
            return
        self._region_drag = {"pos": pos, "a": QPoint(point), "b": QPoint(point)}

    def _region_move(self, point):
        d = self._region_drag
        r = self._page_rect(d["pos"])
        d["b"] = QPoint(min(max(point.x(), r.left()), r.right()), min(max(point.y(), r.top()), r.bottom()))
        self.viewport().update()

    def _region_release(self):
        d, self._region_drag = self._region_drag, None
        self._disarm_region()
        if d is None:
            return
        r = self._page_rect(d["pos"])
        x0, x1 = sorted((d["a"].x(), d["b"].x()))
        y0, y1 = sorted((d["a"].y(), d["b"].y()))
        if x1 - x0 < 6 or y1 - y0 < 6 or r.width() < 1 or r.height() < 1:
            return
        rect = ((x0 - r.x()) / r.width(), (y0 - r.y()) / r.height(), (x1 - r.x()) / r.width(), (y1 - r.y()) / r.height())
        self.cover_region(self._pages[d["pos"]][0], rect)

    def cover_region(self, idx, rect) -> bool:
        """Cover `rect` (fractions of the page) and open a box there to type the replacement into."""
        pos = self._pos_for(idx)
        if pos is None or self._pages[pos][1]:
            return False
        boxes = list(self._texts.get(idx, ()))
        if len(boxes) >= TEXT_BOXES_PER_PAGE_MAX:
            self.textHint.emit(f"A page can hold {TEXT_BOXES_PER_PAGE_MAX} text boxes.")
            return True
        bw, bh = self._sizes[idx]
        base = clean_text_box(dict(self._text_style, x=rect[0], y=rect[1], w=max(TEXT_MIN_W, rect[2] - rect[0]), h=0.02))
        size = min(72.0, max(6.0, round((rect[3] - rect[1]) * bh * 0.72 * 10) / 10.0))
        box = replace(base, size=size, erase=(tuple(rect),), cover=self._sample_cover(idx, [tuple(rect)]),
                      x=rect[0], y=rect[1], w=min(1.0 - rect[0], max(TEXT_MIN_W, rect[2] - rect[0])))
        box = clean_text_box(replace(box, text=""))
        boxes.append(box)
        self._set_boxes(idx, boxes)
        self._select_box(idx, len(boxes) - 1)
        self._begin_edit(idx, len(boxes) - 1)
        self._fresh = None                        # an area marked on purpose stays, even if nothing is typed
        return True

    def delete_existing_text(self, idx, run_no, paragraph=False) -> bool:
        """Take the words of a run (or paragraph) off the page without typing anything in their place."""
        if not self.edit_existing_text(idx, run_no, paragraph) or self._edit is None:
            return False
        self._fresh = None
        self._editor.setPlainText("")
        self._commit_editor()
        return True

    # -- the typing box
    def _begin_edit(self, idx, k):
        from PySide6.QtGui import QTextCursor
        pos = self._pos_for(idx)
        boxes = self._texts.get(idx, ())
        if pos is None or not 0 <= k < len(boxes) or self._pages[pos][1]:
            return
        self._commit_editor()
        ed = _TextBoxEditor(self.viewport())
        ed.setToolTip("Type your text. Esc finishes. Ctrl+B, Ctrl+I and Ctrl+U change the style")
        ed.setAccessibleName("Text box")
        ed.setFrameShape(QFrame.NoFrame)
        ed.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        ed.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        ed.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        ed.setPlainText(boxes[k].text)
        ed.committed.connect(_weak(self._commit_editor))
        ed.formatKey.connect(_weak(self.toggle_text_style))
        ed.textChanged.connect(_weak(self._place_editor))
        self._editor, self._edit = ed, (idx, k)
        self._tsel = (idx, k)
        self._restyle_editor()
        ed.show()
        ed.setFocus()
        ed.moveCursor(QTextCursor.End)
        self.viewport().update()
        self.textStyleChanged.emit()

    def _restyle_editor(self):
        from PySide6.QtGui import QFont
        ed = self._editor
        box = self._texts.get(self._edit[0], ())[self._edit[1]] if self._edit else None
        if ed is None or box is None:
            return
        f = QFont(box.family)
        f.setPixelSize(max(1, int(round(box.size * self._scale))))
        f.setBold(box.bold)
        f.setItalic(box.italic)
        f.setUnderline(box.underline)
        f.setHintingPreference(QFont.PreferNoHinting)       # wraps where the saved text will wrap
        ed.setFont(f)
        from PySide6.QtGui import QTextCursor, QTextBlockFormat
        cur = QTextCursor(ed.document())
        cur.select(QTextCursor.Document)
        bf = QTextBlockFormat()
        bf.setLineHeight(box.spacing * 100.0, 1)            # the same line spacing the page will get
        cur.mergeBlockFormat(bf)
        opt = ed.document().defaultTextOption()
        opt.setAlignment({"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}[box.align])
        ed.document().setDefaultTextOption(opt)
        ed.document().setDocumentMargin(0)
        ed.setStyleSheet(f"QPlainTextEdit {{ color: {box.color}; background: rgba(255,255,255,215); "
                         f"border: 1px solid {theme_color('accent')}; padding: 0px; }}")
        self._place_editor()

    def _place_editor(self, *_a):
        ed = self._editor
        if ed is None or self._edit is None:
            return
        idx, k = self._edit
        pos = self._pos_for(idx)
        boxes = self._texts.get(idx, ())
        if pos is None or not 0 <= k < len(boxes):
            return
        # The box grows with what is typed, measured by the same layout that paints and saves it. (A plain-text
        # editor's own document size is a count of lines, not pixels, so it cannot be used for this.)
        typed = replace(boxes[k], text=ed.toPlainText()[:TEXT_BOX_MAX_CHARS])
        grown = self._autosize(idx, typed)
        if grown != boxes[k]:
            lst = list(boxes)
            lst[k] = grown
            self._texts[idx] = tuple(lst)
            self.viewport().update()
        r = self._box_rect(pos, grown)
        geo = r.adjusted(-1, -1, 1, 1)                      # its 1 px border sits just outside the box, so the text starts where the page text will
        ed.setGeometry(geo)
        bar, extra = ed.verticalScrollBar(), 0
        while bar.maximum() > 0 and extra < 400:            # the editor rounds each line up to whole pixels: give it the few it needs
            extra += 2
            ed.setGeometry(geo.x(), geo.y(), geo.width(), geo.height() + extra)

    def _pull_editor_text(self):
        """Copy what is typed into the model so a style change keeps it."""
        if self._editor is None or self._edit is None:
            return
        idx, k = self._edit
        boxes = list(self._texts.get(idx, ()))
        if k < len(boxes):
            boxes[k] = replace(boxes[k], text=self._editor.toPlainText()[:TEXT_BOX_MAX_CHARS])
            self._texts[idx] = tuple(boxes)

    def _cancel_edit(self):
        ed, self._editor, self._edit = self._editor, None, None
        if ed is not None:
            ed.hide()
            ed.deleteLater()

    def _commit_editor(self):
        if self._editor is None or self._edit is None:
            return
        self._pull_editor_text()
        idx, k = self._edit
        self._cancel_edit()
        fresh, self._fresh = self._fresh, None
        boxes = list(self._texts.get(idx, ()))
        if k < len(boxes):
            b = boxes[k]
            if fresh is not None and fresh[:2] == (idx, k) and _same_text_edit(b, fresh[2]):
                del boxes[k]                                  # opened an old line and changed nothing: not an edit
                if self._tsel == (idx, k):
                    self._tsel = None
            elif not b.text.strip() and not b.erase:
                del boxes[k]
                if self._tsel == (idx, k):
                    self._tsel = None
            else:
                boxes[k] = self._autosize(idx, boxes[k])
        self._set_boxes(idx, boxes)
        self._emit_texts(idx)
        self.textStyleChanged.emit()

    # -- keys
    def _text_key(self, event) -> bool:
        key, mods = event.key(), event.modifiers()
        if mods == Qt.ControlModifier and key == Qt.Key_V and self._edit is None:
            return self.paste_text()
        sel = self._tsel
        if sel is None:
            return False
        idx, k = sel
        boxes = self._texts.get(idx, ())
        if k >= len(boxes):
            return False
        box = boxes[k]
        bw, bh = self._sizes[idx]
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            return self.delete_selected_text()
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_F2) and not mods & Qt.ControlModifier:
            self._begin_edit(idx, k)
            return True
        if mods & Qt.ControlModifier and key in (Qt.Key_B, Qt.Key_I, Qt.Key_U):
            self.toggle_text_style({Qt.Key_B: "bold", Qt.Key_I: "italic", Qt.Key_U: "underline"}[key])
            return True
        if mods & Qt.ControlModifier and key == Qt.Key_D:
            return self.duplicate_selected_text()
        if mods == Qt.ControlModifier and key == Qt.Key_C:
            return self.copy_selected_text()
        if mods == Qt.ControlModifier and key == Qt.Key_X:
            return self.cut_selected_text()
        step = 10.0 if mods & Qt.ShiftModifier else 1.0
        move = {Qt.Key_Left: (-step / bw, 0.0), Qt.Key_Right: (step / bw, 0.0),
                Qt.Key_Up: (0.0, -step / bh), Qt.Key_Down: (0.0, step / bh)}.get(key)
        if move and not mods & Qt.ControlModifier:
            nb = replace(box, x=min(max(0.0, box.x + move[0]), 1.0 - box.w), y=min(max(0.0, box.y + move[1]), 1.0 - box.h))
            lst = list(boxes)
            lst[k] = nb
            self._set_boxes(idx, lst)
            self._emit_texts(idx)
            return True
        return False

    # ------------------------------------------------------------ find
    def find_text(self) -> str:
        return self._needle

    def _find_keys(self):
        if not self._needle:
            return set()
        return {("f", i, r, self._needle) for i, r in self._pages if (i, r) not in self._hits}

    def set_find(self, text, jump=True):
        needle = " ".join((text or "").split())
        if needle == self._needle and needle:
            return
        self._needle = needle
        self._hits.clear()
        self._find_cursor = None
        self._find_jump = bool(jump and needle)
        if needle and self._sizes is not None:
            self._queue_find()
        self._emit_find()
        self.viewport().update()

    def _queue_find(self):
        if not self._needle or self._queue is None:
            return
        cur = self.current_page()
        for pos, (idx, rot) in enumerate(self._pages):
            if (idx, rot) in self._hits:
                continue
            self._queue.push(self._queue.generation, ("f", idx, rot, self._needle),
                             1000 + abs(pos - cur), ("find", idx, self._needle, rot))
        self._find_waiting = len(self._find_keys())

    def _on_found(self, q, key, boxes):
        if q is not self._queue or key[3] != self._needle:
            return
        self._hits[(key[1], key[2])] = list(boxes)
        self._find_waiting = len(self._find_keys())
        if self._find_jump and boxes and (key[1], key[2]) in {(i, r) for i, r in self._pages}:
            self._find_jump = False
            self._find_cursor = (key[1], 0)
            self._scroll_to_hit()
        self._emit_find()
        self.viewport().update()

    def _hit_list(self):
        out = []
        for idx, rot in self._pages:
            for k in range(len(self._hits.get((idx, rot), ()))):
                out.append((idx, k))
        return out

    def _emit_find(self):
        lst = self._hit_list()
        n = lst.index(self._find_cursor) + 1 if self._find_cursor in lst else 0
        self.findChanged.emit(n, len(lst), bool(self._needle) and self._find_waiting == 0)

    def find_step(self, direction):
        lst = self._hit_list()
        if not lst:
            return False
        if self._find_cursor in lst:
            i = (lst.index(self._find_cursor) + (1 if direction > 0 else -1)) % len(lst)
        else:
            i = 0 if direction > 0 else len(lst) - 1
            cur_idx = self._pages[self.current_page()][0] if self._pages else None
            if direction > 0:                     # first hit at or after the page on screen
                order = {pi: n for n, (pi, _r) in enumerate(self._pages)}
                here = order.get(cur_idx, 0)
                after = [j for j, (pi, _k) in enumerate(lst) if order.get(pi, 0) >= here]
                i = after[0] if after else 0
        self._find_cursor = lst[i]
        self._scroll_to_hit()
        self._emit_find()
        self.viewport().update()
        return True

    def _scroll_to_hit(self):
        if self._find_cursor is None or not len(self._lay):
            return
        idx, k = self._find_cursor
        pos = next((n for n, (i, _r) in enumerate(self._pages) if i == idx), None)
        if pos is None:
            return
        boxes = self._hits.get(self._pages[pos], ())
        if k >= len(boxes):
            return
        fx0, fy0, fx1, fy1 = boxes[k]
        h, w = self._lay.heights[pos], self._lay.widths[pos]
        vh, vw = self.viewport().height(), self.viewport().width()
        y = self._lay.tops[pos] + fy0 * h
        self.verticalScrollBar().setValue(int(max(0, y - vh // 3)))
        content_w = max(self._lay.max_width + 2 * self._lay.pad, vw)
        px = (content_w - w) // 2 + fx0 * w
        hs = self.horizontalScrollBar()
        if px < hs.value() or px > hs.value() + vw - 40:
            hs.setValue(int(max(0, px - vw // 3)))


class PdfReaderView(QWidget):
    """Toolbar, page-strip sidebar and the page stack. It owns no PDF state: the
    Page Organizer hands it the plan and connects its buttons to the same
    methods the grid's buttons use."""
    backRequested = Signal()
    rotateRequested = Signal(int)
    deleteRequested = Signal()
    undoRequested = Signal()
    extractRequested = Signal()
    saveRequested = Signal()
    skipRequested = Signal()
    queuePrev = Signal()
    queueNext = Signal()
    reloadRequested = Signal()
    contextRequested = Signal(object)
    selectionChanged = Signal()
    pageActivated = Signal(int)
    textsChanged = Signal(int, object)

    ZOOMS = (("Fit width", READER_FIT_WIDTH), ("Fit page", READER_FIT_PAGE), ("50%", 0.5), ("75%", 0.75),
             ("100%", 1.0), ("125%", 1.25), ("150%", 1.5), ("200%", 2.0), ("300%", 3.0), ("400%", 4.0))

    def __init__(self):
        super().__init__()
        self.setObjectName("readerView")
        root = QVBoxLayout(self)
        self._root = root
        self._fmt = None                              # the text style bar: built the first time Text is switched on
        self._fmt_sync = False
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.back_btn = QPushButton("Back to pages")
        self.back_btn.setObjectName("secondary")
        self.back_btn.setToolTip("Leave the reader and go back to the page grid (Esc). Your page changes are kept")
        self.back_btn.clicked.connect(self.backRequested)
        top.addWidget(self.back_btn)
        self.title = QLabel("")
        self.title.setObjectName("sectionTitle")
        self.title.setAccessibleName("PDF being read")
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # a long file name must not widen the window
        self.title.setMinimumWidth(40)
        top.addWidget(self.title, stretch=1)
        self.dirty_label = QLabel("● Unsaved changes")
        self.dirty_label.setObjectName("hint")
        self.dirty_label.setAccessibleName("Unsaved changes")
        self.dirty_label.setToolTip("Page changes are only written to the file when you press Save & Next PDF (Ctrl+S)")
        self.dirty_label.hide()
        top.addWidget(self.dirty_label)
        # page navigation, where a PDF reader users expect it: ◀ [3] / 8 ▶
        self.prev_btn = QPushButton("◀")
        self.prev_btn.setObjectName("secondary")
        self.prev_btn.setAccessibleName("Previous page")
        self.prev_btn.setToolTip("Go to the previous page")
        self.prev_btn.clicked.connect(self._prev_page)
        top.addWidget(self.prev_btn)
        self.page_spin = QSpinBox()
        self.page_spin.setAccessibleName("Go to page")
        self.page_spin.setToolTip("Type a page number to jump to it (Ctrl+G)")
        self.page_spin.setRange(1, 1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.setMinimumWidth(64)
        self.page_spin.valueChanged.connect(self._spin_changed)
        top.addWidget(self.page_spin)
        self.of_label = QLabel("of 0")
        top.addWidget(self.of_label)
        self.next_btn = QPushButton("▶")
        self.next_btn.setObjectName("secondary")
        self.next_btn.setAccessibleName("Next page")
        self.next_btn.setToolTip("Go to the next page")
        self.next_btn.clicked.connect(self._next_page)
        top.addWidget(self.next_btn)
        self.files_btn = QPushButton("Files")
        self.files_btn.setObjectName("secondary")
        self.files_btn.setAccessibleName("PDFs in the folder")
        self.files_btn.setCheckable(True)
        self.files_btn.setToolTip("Show or hide the list of PDFs in the folder, on the left. Click one to read it here")
        self.files_btn.setChecked(bool(load_ui_state().get("reader_files", True)))
        top.addWidget(self.files_btn)
        self._files_widget = None
        self.sidebar_btn = QPushButton("Strip")
        self.sidebar_btn.setObjectName("secondary")
        self.sidebar_btn.setAccessibleName("Page strip")
        self.sidebar_btn.setCheckable(True)
        self.sidebar_btn.setToolTip("Show or hide the strip of small pages on the left")
        self.sidebar_btn.setChecked(bool(load_ui_state().get("reader_sidebar", True)))
        top.addWidget(self.sidebar_btn)
        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setObjectName("secondary")
        self.zoom_out_btn.setAccessibleName("Zoom out")
        self.zoom_out_btn.setToolTip("Make the pages smaller (Ctrl+Minus)")
        self.zoom_out_btn.clicked.connect(self.zoom_out)
        top.addWidget(self.zoom_out_btn)
        self.zoom_box = QComboBox()
        self.zoom_box.setAccessibleName("Zoom")
        self.zoom_box.setToolTip("How big the pages are drawn. Ctrl+0 fits the page, Ctrl+1 is 100%, "
                                 "Ctrl+2 fits the width; Ctrl+mouse wheel also zooms")
        self.zoom_box.setMinimumWidth(128)
        for label, _v in self.ZOOMS:
            self.zoom_box.addItem(label)
        self.zoom_box.currentIndexChanged.connect(self._zoom_picked)
        top.addWidget(self.zoom_box)
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setObjectName("secondary")
        self.zoom_in_btn.setAccessibleName("Zoom in")
        self.zoom_in_btn.setToolTip("Make the pages bigger (Ctrl+Plus)")
        self.zoom_in_btn.clicked.connect(self.zoom_in)
        top.addWidget(self.zoom_in_btn)
        root.addLayout(top)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.tools = {}
        for key, text, tip, slot in (
                ("rotl", "Rotate left", "Turn the selected pages (or the page on screen) a quarter turn anticlockwise (Ctrl+Shift+L)",
                 lambda: self.rotateRequested.emit(-90)),
                ("rotr", "Rotate right", "Turn the selected pages (or the page on screen) a quarter turn clockwise (Ctrl+Shift+R)",
                 lambda: self.rotateRequested.emit(90)),
                ("del", "Delete page", "Take the selected pages (or the page on screen) out of this PDF. Undo brings them back",
                 self.deleteRequested.emit),
                ("undo", "Undo", "Take back the last change to this PDF's pages (Ctrl+Z)",
                 self.undoRequested.emit),
                ("ext", "Extract…", "Copy the selected pages (or the page on screen) into a new PDF; this PDF is not changed",
                 self.extractRequested.emit)):
            b = QPushButton(text)
            b.setObjectName("secondary")
            b.setToolTip(tip)
            b.clicked.connect(slot)
            self.tools[key] = b
            bar.addWidget(b)
        self._tool_tips = {k: v.toolTip() for k, v in self.tools.items()}
        self.text_btn = QPushButton("Text")
        self.text_btn.setObjectName("secondary")
        self.text_btn.setCheckable(True)
        self.text_btn.setAccessibleName("Add or change text")
        self.text_btn.setToolTip("Add text anywhere, or change the words already on a page. Click empty page and type, or click "
                                 "existing text to retype it. Esc finishes. The PDF is saved as a new file (T switches this on and off)")
        self.text_btn.toggled.connect(self._text_toggled)
        bar.addWidget(self.text_btn)
        bar.addStretch(1)
        self.find_btn = QPushButton("Find")
        self.find_btn.setObjectName("secondary")
        self.find_btn.setCheckable(True)
        self.find_btn.setToolTip("Search the text of this PDF (Ctrl+F)")
        self.find_btn.toggled.connect(self._find_toggled)
        bar.addWidget(self.find_btn)
        root.addLayout(bar)

        self.find_bar = QWidget()
        findrow = QHBoxLayout(self.find_bar)
        findrow.setContentsMargins(0, 0, 0, 0)
        findrow.setSpacing(6)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find in this PDF…")
        self.find_edit.setAccessibleName("Find text in this PDF")
        self.find_edit.setToolTip("Type a word and press Enter to highlight it on every page (Ctrl+F)")
        self.find_edit.setClearButtonEnabled(True)
        self.find_edit.returnPressed.connect(self._find_enter)
        findrow.addWidget(self.find_edit, stretch=1)
        self.find_prev = QPushButton("Previous match")
        self.find_prev.setObjectName("secondary")
        self.find_prev.setToolTip("Jump to the match before this one (Shift+Enter)")
        self.find_prev.clicked.connect(self._find_prev)
        findrow.addWidget(self.find_prev)
        self.find_next = QPushButton("Next match")
        self.find_next.setObjectName("secondary")
        self.find_next.setToolTip("Jump to the next match (Enter)")
        self.find_next.clicked.connect(self._find_next)
        findrow.addWidget(self.find_next)
        self.find_count = QLabel("")
        self.find_count.setObjectName("hint")
        self.find_count.setAccessibleName("Find result")
        self.find_count.setMinimumWidth(70)
        findrow.addWidget(self.find_count)
        self.find_bar.hide()
        root.addWidget(self.find_bar)

        banner = QHBoxLayout()
        self.banner = QLabel("")
        self.banner.setObjectName("status")
        self.banner.setProperty("kind", "error")
        self.banner.setWordWrap(True)
        self.banner.hide()
        banner.addWidget(self.banner, stretch=1)
        self.reload_btn = QPushButton("Reload this PDF")
        self.reload_btn.setObjectName("secondary")
        self.reload_btn.setToolTip("Throw away this PDF's page changes and read the file again from disk")
        self.reload_btn.clicked.connect(self.reloadRequested)
        self.reload_btn.hide()
        banner.addWidget(self.reload_btn)
        root.addLayout(banner)

        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.strip = QListWidget()
        self.strip.setObjectName("readerStrip")
        self.strip.setAccessibleName("Page strip")
        self.strip.setToolTip("Small pages in order. Click one to go to it")
        self.strip.setViewMode(QListWidget.IconMode)
        self.strip.setFlow(QListWidget.TopToBottom)
        self.strip.setWrapping(False)
        self.strip.setMovement(QListWidget.Static)
        self.strip.setResizeMode(QListWidget.Adjust)
        self.strip.setUniformItemSizes(True)
        self.strip.setIconSize(QSize(96, 126))
        self.strip.setGridSize(QSize(132, 158))
        self.strip.setSpacing(2)
        self.strip.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.strip.currentRowChanged.connect(self._strip_row)
        self.strip.setMinimumWidth(150)
        self.strip.setMaximumWidth(190)
        self.split.addWidget(self.strip)
        self.canvas = _ReaderCanvas()
        self.canvas.setToolTip("The pages. Scroll to read, click to select, right-click for page actions")
        self.canvas.pageChanged.connect(self._page_changed)
        self.canvas.selectionChanged.connect(self.selectionChanged)
        self.canvas.contextRequested.connect(self.contextRequested)
        self.canvas.scaleChanged.connect(self._scale_changed)
        self.canvas.findChanged.connect(self._find_changed)
        self.canvas.loaded.connect(self._loaded)
        self.canvas.loadFailed.connect(self._load_failed)
        self.canvas.deleteRequested.connect(self.deleteRequested)
        self.canvas.textsChanged.connect(self.textsChanged)
        self.canvas.textHint.connect(self._text_hint)
        self.canvas.textStyleChanged.connect(self._sync_fmt)
        self.canvas.textToolKey.connect(self._text_key_toggle)
        self.canvas.zoomKey.connect(self._zoom_key)
        self.split.addWidget(self.canvas)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        root.addWidget(self.split, stretch=1)

        foot = QHBoxLayout()
        foot.setSpacing(6)
        self.q_prev_btn = QPushButton("◀ Previous PDF")
        self.q_prev_btn.setObjectName("secondary")
        self.q_prev_btn.setToolTip("Go back to the previous PDF you opened (Ctrl+Page Up)")
        self.q_prev_btn.setAccessibleName("Previous launched PDF")
        self.q_prev_btn.clicked.connect(self.queuePrev)
        self.q_label = QLabel("")
        self.q_label.setObjectName("hint")
        self.q_next_btn = QPushButton("Next PDF ▶")
        self.q_next_btn.setObjectName("secondary")
        self.q_next_btn.setToolTip("Open the next PDF you launched from Explorer (Ctrl+Page Down)")
        self.q_next_btn.setAccessibleName("Next launched PDF")
        self.q_next_btn.clicked.connect(self.queueNext)
        for _w in (self.q_prev_btn, self.q_label, self.q_next_btn):
            foot.addWidget(_w)
            _w.setVisible(False)
        foot.addStretch(1)
        self.save_btn = QPushButton("Save && Next PDF")
        self.save_btn.setObjectName("primaryAction")
        self.save_btn.setToolTip("Write this PDF (Ctrl+S or Ctrl+Enter) and open the next one in the list")
        self.save_btn.clicked.connect(self.saveRequested)
        foot.addWidget(self.save_btn)
        self.skip_btn = QPushButton("Skip")
        self.skip_btn.setObjectName("secondary")
        self.skip_btn.setToolTip("Go to the next PDF without saving this one")
        self.skip_btn.clicked.connect(self.skipRequested)
        foot.addWidget(self.skip_btn)
        root.addLayout(foot)

        self.status = QLabel("")
        self.status.setObjectName("status")
        root.addWidget(self.status)

        self._icon_fn = None
        self._updating = False
        self._custom_zoom_index = None
        self._path = ""
        self.split.setSizes([150, 900])
        self.strip.setVisible(self.sidebar_btn.isChecked())
        self.sidebar_btn.toggled.connect(self._toggle_sidebar)
        self.files_btn.toggled.connect(self._toggle_files)
        self.files_btn.setEnabled(False)
        for _b in (self.prev_btn, self.next_btn, self.zoom_out_btn, self.zoom_in_btn):
            _b.setFixedWidth(38)                     # glyph buttons: no stretched 80 px squares
        chain = [self.back_btn, self.prev_btn, self.page_spin, self.next_btn, self.files_btn, self.sidebar_btn, self.zoom_out_btn,
                 self.zoom_box, self.zoom_in_btn, *self.tools.values(), self.text_btn, self.find_btn, self.find_edit,
                 self.find_prev, self.find_next, self.strip, self.canvas, self.q_prev_btn, self.q_next_btn,
                 self.save_btn, self.skip_btn]
        for a_, b_ in zip(chain, chain[1:]):         # reading order = visual order
            self.setTabOrder(a_, b_)
        self.canvas.pageChanged.connect(self._update_targets)
        self.canvas.selectionChanged.connect(self._update_targets)
        self.canvas.rotateKey.connect(self.rotateRequested)
        self.canvas.goToKey.connect(self._focus_page_spin)
        self.canvas.findKey.connect(self.focus_find)

    # ------------------------------------------------------------ public API
    def open_document(self, path, pages, icon_fn, needle="", start=0):
        self._path = path
        self.title.setText(os.path.basename(path))
        self.title.setToolTip(path)
        self.hide_stale()
        if self.text_btn.isChecked():
            self.text_btn.setChecked(False)
        self.canvas.load(path, pages, needle, start)
        self._icon_fn = icon_fn
        self._fill_strip(pages)
        self.find_edit.setText(needle)
        self._set_page_range(len(pages))
        self._apply_default_zoom()

    def set_pages(self, pages, icon_fn=None):
        if icon_fn is not None:
            self._icon_fn = icon_fn
        self.canvas.set_pages(pages)
        self._fill_strip(pages)
        self._set_page_range(len(pages))

    def refresh_icons(self, only_idx=None):
        if self._icon_fn is None:
            return
        for pos, (idx, _rot) in enumerate(self.canvas.pages()):
            if only_idx is not None and idx != only_idx:
                continue
            it = self.strip.item(pos)
            if it is not None:
                it.setIcon(self._icon_fn(pos))

    def selection(self):
        return self.canvas.selection()

    def current_page(self) -> int:
        return self.canvas.current_page()

    def select_positions(self, positions, notify=True):
        self.canvas.select_positions(positions, notify)

    def go_to(self, pos):
        self.canvas.go_to(pos)

    def set_status(self, text, kind=""):
        self.status.setText(text)
        self.status.setProperty("kind", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def set_enabled_state(self, have_pages: bool, busy: bool, can_undo: bool):
        self.text_btn.setEnabled(have_pages and not busy)
        for k, b in self.tools.items():
            b.setEnabled(have_pages and not busy and (k != "undo" or can_undo))
        self.save_btn.setEnabled(have_pages and not busy)
        self.skip_btn.setEnabled(have_pages and not busy)
        self._q_busy = busy
        self._q_sync()

    def set_queue(self, pos, total, waiting):
        """Show where this PDF sits among the PDFs launched from Explorer."""
        self._q = (pos, total, waiting)
        self._q_sync()

    def _q_sync(self):
        pos, total, waiting = getattr(self, "_q", (-1, 0, 0))
        show = total >= LAUNCH_UI_MIN and pos >= 0
        for _w in (self.q_prev_btn, self.q_label, self.q_next_btn):
            _w.setVisible(show)
        if show:
            self.q_label.setText(f"PDF {pos + 1} of {total} · {waiting} waiting")
            self.q_label.setAccessibleName(f"Launched PDF {pos + 1} of {total}, {waiting} waiting")
            self.skip_btn.setToolTip("Go to the next launched PDF without saving this one")
            self.save_btn.setToolTip("Write this PDF (Ctrl+S or Ctrl+Enter) and open the next launched PDF")
        else:
            self.skip_btn.setToolTip("Go to the next PDF without saving this one")
            self.save_btn.setToolTip("Write this PDF (Ctrl+S or Ctrl+Enter) and open the next one in the list")
        if show:
            free = not getattr(self, "_q_busy", False)
            self.q_prev_btn.setEnabled(free and pos > 0)
            self.q_next_btn.setEnabled(free and pos < total - 1)

    def show_stale(self, text):
        self.banner.setText(text)
        self.banner.show()
        self.reload_btn.show()

    def hide_stale(self):
        self.banner.hide()
        self.reload_btn.hide()

    def stale_shown(self) -> bool:
        return not self.banner.isHidden()

    def stop(self, wait_ms=8000) -> bool:
        return self.canvas.stop(wait_ms)

    def close_document(self, message="Opening…"):
        self.canvas.close_document(message)

    def is_loaded(self) -> bool:
        return self.canvas.is_loaded()

    def focus_find(self):
        self.find_btn.setChecked(True)
        self.find_bar.show()
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def _find_toggled(self, on):
        self.find_bar.setVisible(bool(on))
        if on:
            self.find_edit.setFocus()
        else:
            self.canvas.set_find("")
            self.find_edit.clear()

    def _prev_page(self):
        self.canvas.go_to(self.canvas.current_page() - 1)

    def _next_page(self):
        self.canvas.go_to(self.canvas.current_page() + 1)

    def _focus_page_spin(self):
        self.page_spin.setFocus()
        self.page_spin.selectAll()

    def set_dirty(self, dirty: bool):
        self.dirty_label.setVisible(bool(dirty))

    def _update_targets(self, *_a):
        """Say, in each button's tooltip, which page(s) it will act on."""
        sel = sorted(self.canvas.selection())
        cur = self.canvas.current_page() + 1
        what = (f"page {cur}" if not sel else f"page {sel[0] + 1}" if len(sel) == 1
                else f"the {len(sel)} selected pages")
        for k, b in self.tools.items():
            base = self._tool_tips.get(k, "")
            if k in ("rotl", "rotr", "del", "ext"):
                b.setToolTip(f"Acts on {what}. {base}")

    def handle_escape(self) -> bool:
        """Esc finishes a text box, then lets go of it, then switches the Text tool off; after that it clears
        the find box, then closes the find bar; the next Esc leaves the reader."""
        if self.canvas.text_escape():
            return True
        if self.text_btn.isChecked():
            self.text_btn.setChecked(False)
            self.canvas.setFocus()
            return True
        if self.find_edit.text():
            self.find_edit.clear()
            self.canvas.set_find("")
            self.canvas.setFocus()
            return True
        if self.find_bar.isVisible():
            self.find_btn.setChecked(False)
            self.canvas.setFocus()
            return True
        return False

    # ------------------------------------------------------------ text tool
    def set_texts(self, texts):
        self.canvas.set_texts(texts)

    def _text_key_toggle(self):
        if self.text_btn.isEnabled():
            self.text_btn.setChecked(not self.text_btn.isChecked())

    def add_text_menu(self, menu) -> bool:
        """Text items for the right-click menu: on a box its edit and style actions, on empty page 'Add text here'.
        Returns whether anything was added. Any of them switches the Text tool on first."""
        t = self.canvas.context_target()
        if t is None or not self.text_btn.isEnabled():
            return False
        c = self.canvas
        if t[0] == "seg":
            menu.addAction("Edit this line", _later(self._ctx_do, c.edit_existing_text, t[3], t[4], False))
            menu.addAction("Edit this paragraph\tAlt+click", _later(self._ctx_do, c.edit_existing_text, t[3], t[4], True))
            menu.addAction("Delete this line", _later(self._ctx_do, c.delete_existing_text, t[3], t[4], False))
            menu.addAction("Add new text here", _later(self._ctx_do, c.new_box_at, t[1], t[2]))
            menu.addAction("Cover and retype a region…", _later(self._ctx_do, c.arm_region))
            return True
        if t[0] == "page":
            act = menu.addAction("Add text here", _later(self._ctx_do, c.new_box_at, t[1], t[2]))
            if c.pages()[t[1]][1]:
                act.setEnabled(False)
                act.setText("Add text here (turn the page upright first)")
            else:
                paste = menu.addAction("Paste text here\tCtrl+V", _later(self._ctx_do, c.paste_text, t[1], t[2]))
                paste.setEnabled(c.can_paste_text())
                menu.addAction("Cover and retype a region…", _later(self._ctx_do, c.arm_region))
            return True
        sel = c.selected_text()
        if sel is None:
            return False
        sty = c.text_style()
        menu.addAction("Edit text\tEnter", _later(self._ctx_do, c.edit_selected_text))
        menu.addAction("Duplicate\tCtrl+D", _later(self._ctx_do, c.duplicate_selected_text))
        menu.addAction("Copy\tCtrl+C", _later(self._ctx_do, c.copy_selected_text))
        if not (c._selected_box() is not None and c._selected_box().erase):
            menu.addAction("Cut\tCtrl+X", _later(self._ctx_do, c.cut_selected_text))
        look = QMenu("Style", menu)
        menu.addMenu(look)
        for key, label, tip in (("bold", "Bold", "Ctrl+B"), ("italic", "Italic", "Ctrl+I"), ("underline", "Underline", "Ctrl+U")):
            a = look.addAction(f"{label}\t{tip}", _later(self._ctx_do, c.toggle_text_style, key))
            a.setCheckable(True)
            a.setChecked(bool(sty.get(key)))
        look.addSeparator()
        for key, label in (("left", "Align left"), ("center", "Align centre"), ("right", "Align right")):
            a = look.addAction(label, _later(self._ctx_do, c.set_text_style_kw, "align", key))
            a.setCheckable(True)
            a.setChecked(sty.get("align") == key)
        look.addSeparator()
        sizes = QMenu("Size", look)
        look.addMenu(sizes)
        for v in TEXTBOX_SIZES:
            a = sizes.addAction(str(v), _later(self._ctx_do, c.set_text_style_kw, "size", float(v)))
            a.setCheckable(True)
            a.setChecked(abs(float(sty.get("size", 0)) - v) < 0.01)
        look.addAction("Colour…", _later(self._ctx_do, self._fmt_pick_color))
        order = QMenu("Arrange", menu)
        menu.addMenu(order)
        order.addAction("Bring to front", _later(self._ctx_do, c.reorder_selected_text, True))
        order.addAction("Send to back", _later(self._ctx_do, c.reorder_selected_text, False))
        chosen = c._selected_box()
        menu.addAction("Put the original text back\tDel" if chosen is not None and chosen.erase else "Delete text\tDel",
                       _later(self._ctx_do, c.delete_selected_text))
        return True

    def _ctx_do(self, fn, *args):
        if not self.text_btn.isChecked():
            self.text_btn.setChecked(True)
        fn(*args)

    def _text_hint(self, text):
        self.set_status(text, "")

    def _text_toggled(self, on):
        if on:
            self._ensure_fmt().setVisible(True)
            self.set_status("Text tool on: click empty page to type new text, or click the page's own text to change it. Drag a box to move it, Esc when done.", "")
        elif self._fmt is not None:
            self._fmt.setVisible(False)
        self.canvas.set_text_tool(on)
        self.canvas.setFocus()

    def _ensure_fmt(self):
        """The style bar (font, size, bold/italic/underline, colour, alignment). Built on first use: listing
        every installed font is not something a launch that never adds text should wait for."""
        if self._fmt is not None:
            return self._fmt
        from PySide6.QtWidgets import QFontComboBox
        bar = QWidget()
        bar.setObjectName("textFormatBar")
        lay = FlowLayout(bar, spacing=6)
        self.fmt_font = QFontComboBox()
        self.fmt_font.setAccessibleName("Font")
        self.fmt_font.setToolTip("The font for the selected text box, and for the next one you add")
        self.fmt_font.setMaximumWidth(190)
        self.fmt_font.setCurrentFont(QFont(TEXT_FONT_DEFAULT))
        self.fmt_font.currentFontChanged.connect(self._fmt_font)
        self.fmt_size = QComboBox()
        self.fmt_size.setEditable(True)
        self.fmt_size.setAccessibleName("Font size in points")
        self.fmt_size.setToolTip("Text size in points. Pick one or type a number from 4 to 200")
        for v in TEXTBOX_SIZES:
            self.fmt_size.addItem(str(v))
        self.fmt_size.setCurrentText("12")
        self.fmt_size.setFixedWidth(96)
        self.fmt_size.activated.connect(self._fmt_size_picked)
        self.fmt_size.lineEdit().editingFinished.connect(self._fmt_size_picked)
        for w in (self.fmt_font, self.fmt_size):
            lay.addWidget(w)
        self.fmt_btns = {}
        for key, text, tip in (("bold", "B", "Bold (Ctrl+B)"), ("italic", "I", "Italic (Ctrl+I)"),
                               ("underline", "U", "Underline (Ctrl+U)")):
            b = QPushButton(text)
            b.setObjectName("secondary")
            b.setCheckable(True)
            b.setFixedWidth(36)
            b.setAccessibleName(tip.split(" (")[0])
            b.setToolTip(tip)
            f = b.font()
            f.setBold(key == "bold")
            f.setItalic(key == "italic")
            f.setUnderline(key == "underline")
            b.setFont(f)
            b.toggled.connect(self._fmt_toggled)
            self.fmt_btns[key] = b
            lay.addWidget(b)
        self.fmt_color = QPushButton("Colour")
        self.fmt_color.setObjectName("secondary")
        self.fmt_color.setAccessibleName("Text colour")
        self.fmt_color.setToolTip("Choose the colour of the selected text box, and of the next one you add")
        self.fmt_color.clicked.connect(self._fmt_pick_color)
        lay.addWidget(self.fmt_color)
        self.fmt_align = {}
        grp = QButtonGroup(bar)
        for key, text, tip in (("left", "Left", "Line the text up on the left"),
                               ("center", "Centre", "Centre the text in its box"),
                               ("right", "Right", "Line the text up on the right")):
            b = QPushButton(text)
            b.setObjectName("secondary")
            b.setCheckable(True)
            b.setAccessibleName(f"Align {text.lower()}")
            b.setToolTip(tip)
            grp.addButton(b)
            b.toggled.connect(self._fmt_toggled)
            self.fmt_align[key] = b
            lay.addWidget(b)
        self.fmt_delete = QPushButton("Delete text")
        self.fmt_delete.setObjectName("secondary")
        self.fmt_delete.setAccessibleName("Delete text box")
        self.fmt_delete.setToolTip("Remove the selected text box (Delete). Ctrl+D copies it. Arrow keys nudge it; Shift+arrow moves it further")
        self.fmt_delete.clicked.connect(self.canvas.delete_selected_text)
        lay.addWidget(self.fmt_delete)
        self._root.insertWidget(self._root.indexOf(self.find_bar) + 1, bar)
        self._fmt = bar
        self._sync_fmt()
        return bar

    def _sync_fmt(self):
        if self._fmt is None:
            return
        st = self.canvas.text_style()
        self._fmt_sync = True
        try:
            self.fmt_font.setCurrentFont(QFont(st["family"]))
            self.fmt_size.setCurrentText(f"{st['size']:g}")
            for key, b in self.fmt_btns.items():
                b.setChecked(bool(st[key]))
            for key, b in self.fmt_align.items():
                b.setChecked(st["align"] == key)
            self.fmt_color.setStyleSheet(f"border-bottom: 4px solid {st['color']};")
            self.fmt_delete.setEnabled(self.canvas.selected_text() is not None)
        finally:
            self._fmt_sync = False

    def _fmt_font(self, font):
        if not self._fmt_sync:
            self.canvas.set_text_style(family=font.family())

    def _fmt_size_picked(self, *_a):
        if self._fmt_sync:
            return
        try:
            v = float(self.fmt_size.currentText().replace(",", "."))
        except ValueError:
            self._sync_fmt()
            return
        self.canvas.set_text_style(size=min(max(v, 4.0), 200.0))

    def _fmt_toggled(self, _on=False):
        if self._fmt_sync:
            return
        kw = {k: b.isChecked() for k, b in self.fmt_btns.items()}
        kw["align"] = next((k for k, b in self.fmt_align.items() if b.isChecked()), "left")
        self.canvas.set_text_style(**kw)

    def _fmt_pick_color(self):
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(self.canvas.text_style()["color"]), self, "Text colour")
        if c.isValid():
            self.canvas.set_text_style(color=c.name())
            self.canvas.setFocus()

    # ------------------------------------------------------------ internals
    def _fill_strip(self, pages):
        self._updating = True
        try:
            self.strip.clear()
            for pos in range(len(pages)):
                it = QListWidgetItem(self._icon_fn(pos) if self._icon_fn else QIcon(), f"Page {pos + 1}")
                it.setTextAlignment(Qt.AlignHCenter)
                self.strip.addItem(it)
            self.strip.setCurrentRow(min(self.canvas.current_page(), self.strip.count() - 1))
        finally:
            self._updating = False

    def _set_page_range(self, n):
        self._updating = True
        try:
            self.page_spin.setRange(1, max(1, n))
            self.of_label.setText(f"of {n}")
            self.page_spin.setValue(min(self.canvas.current_page() + 1, max(1, n)))
        finally:
            self._updating = False

    def _loaded(self, n):
        self._set_page_range(n)

    def _load_failed(self, message):
        self.set_status(message, "error")

    def _page_changed(self, pos):
        self._updating = True
        try:
            if pos < self.strip.count():
                self.strip.setCurrentRow(pos)
            self.page_spin.setValue(pos + 1)
        finally:
            self._updating = False
        self.pageActivated.emit(pos)

    def _strip_row(self, row):
        if self._updating or row < 0:
            return
        self.canvas.go_to(row)
        self.canvas.setFocus()

    def _spin_changed(self, v):
        if not self._updating:
            self.canvas.go_to(v - 1)

    def _toggle_sidebar(self, on):
        self.strip.setVisible(bool(on))
        update_ui_state(reader_sidebar=bool(on))

    def dock_files(self, widget):
        """Take the Page Organizer's folder file list in beside the pages, so the PDFs in the folder stay one click away."""
        self._files_widget = widget
        self.split.insertWidget(0, widget)
        widget.setVisible(self.files_btn.isChecked())
        self.files_btn.setEnabled(True)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 0)
        self.split.setStretchFactor(2, 1)
        self.split.setSizes([300, 150, 900])

    def undock_files(self):
        """Let go of the file list (the Page Organizer puts it back where it belongs)."""
        self._files_widget = None
        self.files_btn.setEnabled(False)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)

    def _toggle_files(self, on):
        if self._files_widget is not None:
            self._files_widget.setVisible(bool(on))
        update_ui_state(reader_files=bool(on))

    def _apply_default_zoom(self):
        d = setting("reader_default_zoom")
        mode = {"width": READER_FIT_WIDTH, "page": READER_FIT_PAGE}.get(d, 1.0)
        self.canvas.set_zoom(mode)
        self._show_zoom(mode)

    def _show_zoom(self, mode):
        self.zoom_box.blockSignals(True)
        try:
            for i, (_l, v) in enumerate(self.ZOOMS):
                if v == mode:
                    self._drop_custom()
                    if isinstance(v, str):               # a fit mode: say what percentage it came to
                        try:
                            self.zoom_box.setItemText(i, f"{_l} ({round(self.canvas.scale() * 100)}%)")
                        except Exception:
                            self.zoom_box.setItemText(i, _l)
                    self.zoom_box.setCurrentIndex(i)
                    return
            pct = f"{round(float(mode) * 100)}%"
            if self._custom_zoom_index is None:
                self.zoom_box.addItem(pct)
                self._custom_zoom_index = self.zoom_box.count() - 1
            else:
                self.zoom_box.setItemText(self._custom_zoom_index, pct)
            self.zoom_box.setCurrentIndex(self._custom_zoom_index)
        finally:
            self.zoom_box.blockSignals(False)

    def _drop_custom(self):
        if self._custom_zoom_index is not None:
            self.zoom_box.removeItem(self._custom_zoom_index)
            self._custom_zoom_index = None

    def _zoom_picked(self, i):
        if 0 <= i < len(self.ZOOMS):
            self.canvas.set_zoom(self.ZOOMS[i][1])
            self._drop_custom()

    def _scale_changed(self, scale):
        self._show_zoom(self.canvas.zoom_mode())

    def zoom_in(self):
        self._zoom_key(1)

    def zoom_out(self):
        self._zoom_key(-1)

    def _zoom_key(self, direction):
        if direction == 0:
            new = READER_FIT_PAGE                         # Ctrl+0: fit page, as in Acrobat
        elif direction == 100:
            new = 1.0                                     # Ctrl+1: actual size
        elif direction == 101:
            new = READER_FIT_WIDTH                        # Ctrl+2: fit width
        else:
            new = reader_zoom_step(self.canvas.scale(), direction)
        self.canvas.set_zoom(new)
        self._show_zoom(new)

    def _find_enter(self):
        text = self.find_edit.text().strip()
        if not text:
            self.canvas.set_find("")
            return
        if " ".join(text.split()) != self.canvas.find_text():
            self.canvas.set_find(text)
        elif QApplication.keyboardModifiers() & Qt.ShiftModifier:
            self.canvas.find_step(-1)
        else:
            self.canvas.find_step(1)

    def _find_next(self):
        if self.find_edit.text().strip() and " ".join(self.find_edit.text().split()) != self.canvas.find_text():
            self.canvas.set_find(self.find_edit.text())
        else:
            self.canvas.find_step(1)

    def _find_prev(self):
        if self.find_edit.text().strip() and " ".join(self.find_edit.text().split()) != self.canvas.find_text():
            self.canvas.set_find(self.find_edit.text())
        else:
            self.canvas.find_step(-1)

    def _find_changed(self, cur, total, done):
        if not self.canvas.find_text():
            self.find_count.setText("")
        elif total == 0:
            self.find_count.setText("No matches." if done else "Searching…")
        else:
            self.find_count.setText(f"{cur} of {total}" + ("" if done else "+"))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
            self.focus_find()
            event.accept()
            return
        super().keyPressEvent(event)


class ExtractDialog(QDialog):
    """Where should the extracted pages go, and what should the file be called?
    Nothing is written until OK. The result is (folder, file name without
    .pdf); an existing file is never replaced, it is numbered instead."""

    def __init__(self, parent, start_dir: str, default_name: str, n_pages: int,
                 can_delete: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Extract Pages")
        self.setModal(True)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        intro = QLabel(f"Copy {plural(n_pages, 'page')} into a new PDF. The original is not changed.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        row = QHBoxLayout()
        self.folder_edit = QLineEdit(start_dir)
        self.folder_edit.setAccessibleName("Folder to save the extracted pages in")
        self.folder_edit.textChanged.connect(self._refresh)
        row.addWidget(self.folder_edit, stretch=1)
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setObjectName("secondary")
        self.browse_btn.clicked.connect(self._browse)
        row.addWidget(self.browse_btn)
        form.addRow("Save in:", row)
        self.name_edit = QLineEdit(default_name)
        self.name_edit.setAccessibleName("File name for the extracted pages")
        self.name_edit.textChanged.connect(self._refresh)
        form.addRow("File name:", self.name_edit)
        lay.addLayout(form)
        self.delete_box = QCheckBox("Delete these pages from the PDF after extracting")
        self.delete_box.setAccessibleName("Delete the extracted pages from the PDF after extracting")
        self.delete_box.setChecked(bool(can_delete and setting("organizer_extract_delete_default")))
        self.delete_box.setEnabled(can_delete)
        lay.addWidget(self.delete_box)
        self.delete_hint = QLabel(
            "Takes the extracted pages out of the PDF you are organizing. Undo brings them back, "
            "and the original file changes only when you Save (the copy goes in the Reorganized folder)."
            if can_delete else
            "Every page is selected, and a PDF needs at least one page, so the pages cannot be deleted afterwards.")
        self.delete_hint.setObjectName("hint")
        self.delete_hint.setWordWrap(True)
        lay.addWidget(self.delete_hint)
        self.note = QLabel("")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Extract")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.name_edit.selectAll()
        self.name_edit.setFocus()
        self._refresh()
        apply_tooltips(self)

    def _browse(self):
        start = self.folder_edit.text().strip()
        folder = QFileDialog.getExistingDirectory(
            self, "Choose where to save the extracted pages", start if os.path.isdir(start) else "")
        if folder:
            self.folder_edit.setText(folder)

    def problem(self) -> str:
        folder = self.folder_edit.text().strip()
        name = self.name_edit.text().strip()
        if name.lower().endswith(".pdf"):
            name = name[:-4]
        if not folder or not os.path.isdir(folder):
            return "That folder does not exist."
        if not valid_name_component(name):
            return "That file name cannot be used. Avoid  \\ / : * ? \" < > |  and names ending in a dot."
        return ""

    def _refresh(self, *_a):
        bad = self.problem()
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setEnabled(not bad)
        if bad:
            self.note.setText(bad)
            return
        name, folder = self.result_name(), self.folder_edit.text().strip()
        full = os.path.join(folder, name + ".pdf")
        self.note.setText(
            f"A file named {name}.pdf is already there, so this one will be numbered "
            "(for example “name (1).pdf”). Nothing is replaced." if os.path.exists(full)
            else f"Will be saved as {full}")

    def result_name(self) -> str:
        name = self.name_edit.text().strip()
        return name[:-4] if name.lower().endswith(".pdf") else name

    def result(self):
        return self.folder_edit.text().strip(), self.result_name()

    def delete_after(self) -> bool:
        return bool(self.delete_box.isEnabled() and self.delete_box.isChecked())


LAUNCH_QUEUE_MAX = 200      # PDFs handed to the app by Explorer that one session will queue
LAUNCH_UI_MIN = 2           # the queue controls appear from this many PDFs
LAUNCH_BURST_S = 8.0        # launches closer together than this are one batch: none of them steals the view
_LAUNCH_TARGET = "\0launch:"


class OrganizerPanel(_WorkerPanelMixin, QWidget):
    SHORTCUT_OPEN = "browse_folder"
    SHORTCUT_RUN = "save_and_next"
    SHORTCUT_SAVE = "save_if_dirty"
    SHORTCUT_UNDO = "undo_pages"
    SHORTCUT_REFRESH = "refresh_list"
    SYNC_MIN_GAP = 2.0             # seconds between automatic folder re-reads
    SYNC_TIMER_MS = 10000
    UNDO_MAX = ADVANCED_DEFAULTS["organizer_undo_depth"]     # the default; Settings > Advanced overrides
    _extract_pending = None        # set of page idx to delete once an extract succeeds
    _extract_delete = False        # the dialog's "delete after extracting" answer
    _ROLE_IDX = Qt.UserRole

    def __init__(self):
        super().__init__()
        self._busy = False                 # a SAVE is running (the only unsafe moment to close)
        self._worker = None                # the save worker
        self._bg = []                      # background workers (thumbs / info / search), cancellable
        self._token = 0                    # folder generation (info/search)
        self._thumb_token = 0              # page-render generation
        self._search_gen = 0               # search generation
        self._row_of = {}                  # path -> list row; rows only change in _load_folder
        self._op_path = ""                 # file a running save/extract belongs to
        self._folder_loaded = ""
        self._cur_path = ""
        self._cur_stamp = None
        self._pages = []                   # the pages kept, in order: [{"idx", "rot", "blank", "match"}]
        self._undo = []                    # snapshots of [(idx, rot)] for the pages kept, oldest first; ("T", idx, old boxes) for a text change
        self._texts = {}                   # source page index -> tuple of TextBox added in the reader (written on save)
        self._text_note_shown = False
        self._all = {}                     # idx -> page dict for EVERY page of the open PDF (deleted pages come back from here)
        self._clip_epoch = 0               # bumped when the open PDF changes: late pictures for pasted pages are dropped
        self._next_token = PASTE_BASE      # the next number given to a pasted page
        self._preview_path = ""            # temp PDF the reader is showing while pasted pages are in (read-only)
        self._preview_epoch = 0
        self._preview_at = 0
        self._b_path = ""                  # the B pane's PDF (a read-only source), "" when none
        self._b_stamp = None
        self._b_count = 0
        self._b_thumbs = {}                # source page -> QImage
        self._b_epoch = 0                  # late pictures for a B that was replaced are dropped
        self._b_serial = 0                 # a drag from an old B is ignored
        self._total = 0                    # page count of the open PDF as it is on disk
        self._thumbs = {}                  # idx -> QImage (as rendered)
        self._file_state = {}              # path -> "saved" | "skipped" | "new" (extracted here) | "added" | "changed" | "gone"
        self._row_sig = {}                 # path -> (size, mtime_ns) as last seen in the folder
        self._last_sync = 0.0
        self._sync_running = False
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(self.SYNC_TIMER_MS)
        self._sync_timer.timeout.connect(self._on_sync_timer)
        QApplication.instance().applicationStateChanged.connect(_weak(self._on_app_state))
        self._counts = {}                  # path -> page count
        self._matches = {}                 # path -> search result dict
        self._search_text = ""
        self._notice_shown = False
        self._loading_row = False
        self.reader = None                 # PdfReaderView, built the first time a PDF is opened full size
        self._zoom_w = self._load_zoom()    # thumbnail width chosen with Ctrl+wheel (None = the Settings size)
        self._hi = OrderedDict()           # page index -> sharper picture drawn for the pages in view when zoomed in
        self._hi_w = 0
        self._hi_epoch = 0
        self._live = (0, ORG_LIVE_ROWS)    # rows whose pictures are kept at full size while zoomed far in
        self._launch_q = []                # PDFs handed over by Explorer, in arrival order (never kept after the app closes)
        self._launch_state = {}            # path -> "waiting" | "viewed" | "missing" | "failed"
        self._launch_pos = -1
        self._launch_dir = 1               # the way the operator last stepped: a PDF that cannot open moves on this way
        self._launch_t = 0.0               # monotonic time of the last launch (batch detection)
        self._launch_guard = False         # a save prompt is open: launches that arrive now are queued, not shown
        self._reader_want = None           # (path, monotonic time, page position): open the reader once that PDF's pages are loaded

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)
        organizer_page = QWidget()
        self.stack.addWidget(organizer_page)
        root, actions = ink_shell(organizer_page)
        self.inspector = organizer_page.inspector
        root.setSpacing(12)

        header = QLabel("Page Organizer")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        root.addWidget(header)
        root.addSpacing(8)                 # same title-to-first-section gap as the other tabs

        step1 = QLabel("1. CHOOSE A FOLDER OF PDFs")
        step1.setObjectName("sectionTitle")
        root.addWidget(step1)
        self.folder_row = FolderRow("Choose Folder", self.browse_folder,
                                    recent_key="organizer_folders")
        root.addWidget(self.folder_row)

        step2 = QLabel("2. PICK A PDF, FIX ITS PAGES, THEN SAVE & NEXT")
        step2.setObjectName("sectionTitle")
        root.addWidget(step2)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        # ---- left: files + search ----
        left = QFrame()
        left.setObjectName("panel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 10, 10, 10)
        ll.setSpacing(8)
        srow = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Find text in these PDFs…")
        self.search_edit.setAccessibleName("Search the text of every PDF in the folder")
        self.search_edit.returnPressed.connect(self.run_search)
        srow.addWidget(self.search_edit, stretch=1)
        ll.addLayout(srow)
        srow = QHBoxLayout()               # second row: the buttons never squeeze the box
        self.search_btn = QPushButton("Search")
        self.search_btn.setObjectName("secondary")
        self.search_btn.clicked.connect(self.run_search)
        srow.addWidget(self.search_btn)
        self.clear_search_btn = QPushButton("Clear")
        self.clear_search_btn.setObjectName("secondary")
        self.clear_search_btn.clicked.connect(self.clear_search)
        srow.addWidget(self.clear_search_btn)
        self.only_matches = QCheckBox("Only PDFs with a match")
        self.only_matches.toggled.connect(self._apply_filter)
        orow = srow
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("secondary")
        self.refresh_btn.setAccessibleName("Refresh the PDF list")
        self.refresh_btn.clicked.connect(self.refresh_list)
        orow.addWidget(self.refresh_btn)
        ll.addLayout(srow)
        ll.addWidget(self.only_matches)
        self.launch_panel = QWidget()
        lp = QVBoxLayout(self.launch_panel)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(4)
        self.launch_label = QLabel("")
        self.launch_label.setObjectName("hint")
        self.launch_label.setWordWrap(True)
        lp.addWidget(self.launch_label)
        lrow = QHBoxLayout()
        lrow.setSpacing(6)
        self.launch_prev_btn = QPushButton("◀ Previous")
        self.launch_prev_btn.setObjectName("secondary")
        self.launch_prev_btn.setAccessibleName("Previous launched PDF")
        self.launch_prev_btn.setToolTip("Go back to the previous PDF you opened (Ctrl+Page Up)")
        self.launch_prev_btn.clicked.connect(self._launch_prev)
        self.launch_next_btn = QPushButton("Next ▶")
        self.launch_next_btn.setObjectName("secondary")
        self.launch_next_btn.setAccessibleName("Next launched PDF")
        self.launch_next_btn.setToolTip("Open the next PDF you launched from Explorer (Ctrl+Page Down)")
        self.launch_next_btn.clicked.connect(self._launch_next)
        self.launch_clear_btn = QPushButton("Clear viewed")
        self.launch_clear_btn.setObjectName("secondary")
        self.launch_clear_btn.setToolTip("Take the PDFs you have already looked at off this list")
        self.launch_clear_btn.clicked.connect(self.launch_clear_viewed)
        for _b in (self.launch_prev_btn, self.launch_next_btn, self.launch_clear_btn):
            _b.setStyleSheet("padding-left: 8px; padding-right: 8px;")
            lrow.addWidget(_b)
        lp.addLayout(lrow)
        self.launch_list = QListWidget()
        self.launch_list.setAccessibleName("PDFs opened from Explorer")
        self.launch_list.setToolTip("PDFs you opened from Windows. Click one to read it; a check mark means you have opened it")
        self.launch_list.setMaximumHeight(150)
        self.launch_list.itemClicked.connect(self._on_launch_clicked)
        lp.addWidget(self.launch_list)
        self.launch_panel.setVisible(False)
        ll.addWidget(self.launch_panel)
        for _seq, _slot in (("Ctrl+PgDown", self._launch_next), ("Ctrl+PgUp", self._launch_prev)):
            _sc = QShortcut(QKeySequence(_seq), self)
            _sc.setContext(Qt.WidgetWithChildrenShortcut)
            _sc.activated.connect(_slot)
        self.file_list = _FileDragList()
        self.file_list.setAccessibleName("PDFs in the folder")
        self.file_list.currentItemChanged.connect(self._on_file_changed)
        self.file_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._file_menu)
        self.file_list.itemDoubleClicked.connect(self._on_file_double_clicked)
        self.file_list.installEventFilter(self)
        self.setAcceptDrops(True)
        ll.addWidget(self.file_list, stretch=1)
        self.file_hint = QLabel("Searches the text already inside each PDF. A scan with no "
                                "text layer is marked “no text”. Double-click a PDF to read it full size.")
        self.file_hint.setObjectName("hint")
        self.file_hint.setWordWrap(True)
        ll.addWidget(self.file_hint)
        left.setMinimumWidth(260)
        split.addWidget(left)

        # ---- right: toolbar + grid ----
        right = QFrame()
        right.setObjectName("panel")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(10, 10, 10, 10)
        rl.setSpacing(8)
        # One row. The buttons shrink to their text (small side padding) so all
        # eight fit the pane at its default width without pushing any off the edge.
        bar = _GridAdder(2)            # one line; wraps only in a pane too narrow for eight labels
        self.tools = {}
        for key, text, tip, slot in (
                ("rotl", "Rotate left", "Turn the selected pages a quarter turn anticlockwise",
                 self.rotate_left),
                ("rotr", "Rotate right", "Turn the selected pages a quarter turn clockwise",
                 self.rotate_right),
                ("del", "Delete page", "Take the selected pages out of this PDF (Delete key). Undo brings them back",
                 self.delete_pages),
                ("up", "Earlier", "Move the selected pages one place earlier", self.move_earlier),
                ("down", "Later", "Move the selected pages one place later", self.move_later),
                ("undo", "Undo", "Take back the last change to this PDF's pages (Ctrl+Z)",
                 self.undo_pages),
                ("ext", "Extract…", "Copy the selected pages into a new PDF; this PDF is not changed",
                 self.extract_selected),
                ("reset", "Reset", "Put this PDF back to how it was when opened (Undo can bring the changes back)",
                 self.reset_pages)):
            b = QPushButton(text)
            b.setObjectName("secondary")
            b.setToolTip(tip)
            b.clicked.connect(slot)
            self.tools[key] = b
            bar.addWidget(b)
        self.compare_btn = QPushButton("Compare\u2026")
        self.compare_btn.setObjectName("secondary")
        self.compare_btn.setToolTip("Show a second PDF (B) beside this one, to take pages from it")
        self.compare_btn.clicked.connect(self.toggle_compare)
        bar.addWidget(self.compare_btn)
        self._tools_grid = bar.grid
        self.grid = _PageGrid()
        self.grid.pagesDropped.connect(self._on_pages_dropped)
        self.grid.deleteRequested.connect(self.delete_pages)
        self.grid.cutRequested.connect(self.cut_pages)
        self.grid.copyRequested.connect(self.copy_pages)
        self.grid.pasteRequested.connect(self.paste_pages)
        self.grid.customContextMenuRequested.connect(self._grid_menu)
        self.grid.itemSelectionChanged.connect(self._update_info)
        self.grid.itemDoubleClicked.connect(self._on_page_double_clicked)
        self.grid.installEventFilter(self)
        self.grid.foreignDropped.connect(self._on_b_dropped)
        self.grid.filesDropped.connect(self._on_files_dropped)
        self.grid.zoomRequested.connect(self.zoom_by)
        self.grid.zoomReset.connect(self.zoom_reset)
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.setInterval(140)
        self._zoom_timer.timeout.connect(self._zoom_settle)
        self.grid.verticalScrollBar().valueChanged.connect(self._zoom_timer_start)
        self.pages_split = QSplitter(Qt.Horizontal)
        self.pages_split.setChildrenCollapsible(False)
        self.pages_split.setHandleWidth(8)
        self.pages_split.addWidget(self.grid)
        self.b_pane = _ComparePane()
        self.b_pane.setVisible(False)
        self.b_pane.chooseRequested.connect(self.choose_b)
        self.b_pane.swapRequested.connect(self.swap_ab)
        self.b_pane.copyRequested.connect(self.b_copy)
        self.b_pane.insertRequested.connect(self.b_insert)
        self.b_pane.closeRequested.connect(self.close_b)
        self.b_pane.pathsDropped.connect(self._on_b_files_dropped)
        self.b_pane.grid.copyRequested.connect(self.b_copy)
        self.b_pane.grid.itemDoubleClicked.connect(self._on_b_double_clicked)
        self.b_pane.grid.customContextMenuRequested.connect(self._b_menu)
        self.pages_split.addWidget(self.b_pane)
        self.pages_split.setStretchFactor(0, 1)
        self.pages_split.setStretchFactor(1, 1)
        rl.addWidget(self.pages_split, stretch=1)
        info_row = QHBoxLayout()
        info_row.setSpacing(6)
        self.info_label = QLabel("Choose a folder, then click a PDF.")
        self.info_label.setObjectName("hint")
        self.info_label.setWordWrap(True)
        info_row.addWidget(self.info_label, stretch=1)
        self.zoom_label = QLabel("")
        self.zoom_label.setObjectName("hint")
        self.zoom_label.setAccessibleName("Thumbnail size")
        self.zoom_label.setToolTip("How wide each page picture is. Hold Ctrl and turn the mouse wheel over the pages to change it")
        info_row.addWidget(self.zoom_label)
        self.zoom_out_btn = QPushButton("\u2212")
        self.zoom_out_btn.setObjectName("secondary")
        self.zoom_out_btn.setAccessibleName("Smaller page pictures")
        self.zoom_out_btn.setToolTip("Make the page pictures smaller (Ctrl+Minus, or Ctrl+mouse wheel down)")
        self.zoom_out_btn.setFixedWidth(38)
        self.zoom_out_btn.clicked.connect(self._zoom_out_clicked)
        info_row.addWidget(self.zoom_out_btn)
        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setObjectName("secondary")
        self.zoom_in_btn.setAccessibleName("Larger page pictures")
        self.zoom_in_btn.setToolTip("Make the page pictures bigger so a signature can be read (Ctrl+Plus, or Ctrl+mouse wheel up)")
        self.zoom_in_btn.setFixedWidth(38)
        self.zoom_in_btn.clicked.connect(self._zoom_in_clicked)
        info_row.addWidget(self.zoom_in_btn)
        self.zoom_reset_btn = QPushButton("Reset size")
        self.zoom_reset_btn.setObjectName("secondary")
        self.zoom_reset_btn.setToolTip("Back to the page picture size in Settings (Ctrl+0)")
        self.zoom_reset_btn.clicked.connect(self.zoom_reset)
        info_row.addWidget(self.zoom_reset_btn)
        rl.addLayout(info_row)
        self._zoom_sync_ui()
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 900])
        root.addWidget(split, stretch=1)
        self._files_panel = left
        self._main_split = split
        self.stack.currentChanged.connect(self._dock_sync)

        btns = actions
        self.save_btn = QPushButton("Save && Next PDF")
        self.save_btn.setObjectName("primaryAction")
        self.save_btn.setToolTip("Write this PDF (Ctrl+Enter) and open the next one in the list")
        self.save_btn.clicked.connect(self.save_and_next)
        btns.addWidget(self.save_btn)
        self.skip_btn = QPushButton("Skip")
        self.skip_btn.setObjectName("secondary")
        self.skip_btn.setToolTip("Go to the next PDF without saving this one")
        self.skip_btn.clicked.connect(self.skip_next)
        btns.addWidget(self.skip_btn)
        self.open_btn = QPushButton("Open Output Folder")
        self.open_btn.setObjectName("secondary")
        self.open_btn.clicked.connect(self._open_output)
        btns.addWidget(self.open_btn)
        cap2 = QLabel("PAGE TOOLS")
        cap2.setObjectName("sectionTitle")
        actions.addSpacing(8)
        actions.addWidget(cap2)
        actions.addLayout(self._tools_grid)
        actions.addStretch(1)

        self.status = QLabel("Ready")
        self.status.setObjectName("status")
        root.addWidget(self.status)

        self._set_tools_enabled(False)
        last = load_ui_state().get("organizer_last_folder", "")
        self.folder_row.changed.connect(self._on_folder_changed)
        if isinstance(last, str) and last:
            self.folder_row.set_path(last, remember=False)

    # ------------------------------------------------------------ plumbing
    def _set_status(self, text: str, kind: str = ""):
        self.status.setText(text)
        self.status.setProperty("kind", kind)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        if self.reader is not None:
            self.reader.set_status(text, kind)

    def _thumb_width(self) -> int:
        return int(setting("organizer_thumb_size"))

    def _track(self, worker):
        _reap_alive_workers()
        _ALIVE_WORKERS.add(worker)
        worker.finished.connect(_reap_alive_workers)
        self._bg.append(worker)
        worker.finished.connect(self._reap_workers)
        return worker

    def _reap_workers(self):
        self._bg = [w for w in self._bg if w.isRunning()]

    def _cancel_background(self):
        self._token += 1                   # anything still in flight is now stale
        self._thumb_token += 1
        self._search_gen += 1
        for w in self._bg:
            w.cancel()

    def stop_for_shutdown(self, wait_ms=15000):
        self._shutting_down = True
        self._cancel_background()
        ok = True
        for w in list(self._bg):
            ok = w.wait(wait_ms) and ok
        if self._worker is not None:
            self._worker.cancel()
            ok = self._worker.wait(wait_ms) and ok
        if getattr(self, "_preview_path", ""):
            try:
                os.remove(self._preview_path)
            except OSError:
                pass
            self._preview_path = ""
        return ok

    def _searching(self) -> bool:
        return any(isinstance(w, _SearchWorker) and w.isRunning() for w in self._bg)

    def is_busy(self):
        return bool(self._busy or self._searching())

    def has_unsaved(self) -> bool:
        return self._is_dirty()

    def stop_background(self, wait_ms=8000) -> bool:
        """Cancel and wait for the page renderer / counter / search. Used on
        window close: a live QThread destroyed with its window aborts."""
        self._cancel_background()
        ok = True
        for w in list(self._bg):
            ok = w.wait(wait_ms) and ok
        if self.reader is not None:
            ok = self.reader.stop(wait_ms) and ok
        return ok

    def _cancel(self):
        """Esc: stop a running search. A running save is never interrupted."""
        if self._searching():
            self._search_gen += 1
            for w in self._bg:
                if isinstance(w, _SearchWorker):
                    w.cancel()
            self._set_status("Search stopped.", "")

    def _set_tools_enabled(self, on: bool):
        for b in self.tools.values():
            b.setEnabled(on)
        self.save_btn.setEnabled(on)
        self.skip_btn.setEnabled(on and bool(self.file_list.count()))
        self._sync_undo()
        self._reader_sync_state()

    def _sync_undo(self):
        b = self.tools.get("undo")
        if b is not None:
            b.setEnabled(bool(self._undo) and bool(self._pages) and not self._busy)
        self._reader_sync_state()

    def _snapshot(self):
        return [(p["idx"], p["rot"], bool(p.get("cut"))) for p in self._pages]

    def _push_undo(self):
        """Call BEFORE a change to the pages, and only when it will change something."""
        snap = self._snapshot()
        if self._undo and self._undo[-1] == snap:
            return
        self._undo.append(snap)
        del self._undo[:-int(setting("organizer_undo_depth"))]
        self._sync_undo()

    def undo_pages(self):
        if self._busy or not self._undo or not self._pages:
            return
        snap = self._undo.pop()
        if isinstance(snap, tuple) and snap and snap[0] == "T":
            self._undo_text(snap[1], snap[2])
            return
        if not snap or any(i not in self._all for i, _r, _c in snap):
            self._undo = []                          # the page set changed under us: nothing safe to restore
            self._sync_undo()
            return
        keep = self._selected_idx()
        pages = []
        for i, rot, cut in snap:
            pg = self._all[i]
            pg["rot"] = rot
            if cut:
                pg["cut"] = True
            else:
                pg.pop("cut", None)
            pages.append(pg)
        self._pages = pages
        flagged = [p["idx"] for p in pages if p.get("cut")]
        if flagged and len(_PAGE_CLIP):
            _PAGE_CLIP.cut = {"owner": id(self), "tokens": flagged}       # the cut is back: Ctrl+V moves them again
        elif not flagged and _PAGE_CLIP.cut and _PAGE_CLIP.cut.get("owner") == id(self):
            _PAGE_CLIP.cut = None
        else:
            for p in pages:
                p.pop("cut", None)                         # a cut with nothing on the clipboard is not a cut
        self._rebuild_grid(keep)
        self._sync_undo()
        self._set_status("Undid the last change.", "")

    # ------------------------------------------------------------ text added in the reader
    def _has_texts(self) -> bool:
        return any(self._texts.get(p["idx"]) for p in self._pages if not self._pasted(p) and not p.get("cut"))

    def _texts_for_plan(self):
        """One tuple of TextBox per planned page, in plan order; None when there is no text to write."""
        if not self._has_texts():
            return None
        return [tuple(self._texts.get(p["idx"], ())) if not self._pasted(p) else () for p in self._pages
                if not p.get("cut")]

    def _texts_changed(self, idx, new):
        if self._busy or idx not in self._all:
            return
        new = tuple(new)
        old = tuple(self._texts.get(idx, ()))
        if new == old:
            return
        self._undo.append(("T", idx, old))
        del self._undo[:-int(setting("organizer_undo_depth"))]
        if new:
            self._texts[idx] = new
        else:
            self._texts.pop(idx, None)
        self._sync_undo()
        if not self._text_note_shown and self._has_texts():
            self._text_note_shown = True
            if self.reader is not None:
                self.reader.set_status("Text is written when you press Save. It is saved as a new file ending in "
                                       "_edited; this PDF is not changed.", "")

    def _undo_text(self, idx, old):
        if idx not in self._all:
            self._undo = []
            self._sync_undo()
            return
        if old:
            self._texts[idx] = tuple(old)
        else:
            self._texts.pop(idx, None)
        if self.reader is not None:
            self.reader.set_texts(self._texts)
        self._sync_undo()
        self._set_status("Undid the last text change.", "")

    def _begin_op(self, path):
        self._busy = True
        self._op_path = path
        self._set_tools_enabled(False)
        self.folder_row.setEnabled(False)

    def _end_op(self):
        self._busy = False
        self.folder_row.setEnabled(True)
        self._reader_sync_state()

    # ------------------------------------------------------------ folder / files
    def browse_folder(self):
        start = self.folder_row.path()
        folder = QFileDialog.getExistingDirectory(
            self, "Choose a folder of PDFs", start if os.path.isdir(start) else "")
        if folder:
            self.folder_row.set_path(folder)

    def _on_folder_changed(self, folder: str):
        if not folder:
            return
        if self._busy:
            self.folder_row.path_label.setText(self._folder_loaded or "No folder selected")
            return
        if self._is_dirty() and not self._confirm_leave("a different folder"):
            # The row already shows the new folder; put it back so the path,
            # the list and Open Output Folder all describe the same place.
            self.folder_row.path_label.setText(self._folder_loaded or "No folder selected")
            return
        update_ui_state(organizer_last_folder=folder)
        self._load_folder(folder)

    def _load_folder(self, folder: str):
        self._folder_loaded = folder
        self._cancel_background()
        self._clear_grid()
        self.file_list.blockSignals(True)
        self.file_list.clear()
        self._file_state.clear()
        self._counts.clear()
        self._matches.clear()
        self._row_of = {}
        self._row_sig = {}
        self._last_sync = time.monotonic()
        listing = pdf_folder_listing(folder)
        for d in listing:
            self._row_sig[d["path"]] = (d["size"], d.get("mtime_ns"))
            it = QListWidgetItem()
            it.setData(Qt.UserRole, d["path"])
            self._row_of.setdefault(d["path"], self.file_list.count())
            self.file_list.addItem(it)
            self._refresh_row(it, d["size"])
        self.file_list.blockSignals(False)
        self._set_tools_enabled(False)
        if not listing:
            self._set_status("There are no PDFs directly in that folder.", "error")
            self.info_label.setText("No PDFs found. Subfolders are not searched.")
            return
        self._set_status(f"{plural(len(listing), 'PDF')} in this folder. Click one to see its pages.", "")
        self.info_label.setText("Click a PDF on the left to see its pages.")
        self.skip_btn.setEnabled(True)
        w = self._track(_InfoWorker([d["path"] for d in listing], self._token))
        w.info.connect(self._on_info)
        w.start()
        if self._search_text:
            self.run_search()

    def _row_path(self, item) -> str:
        return item.data(Qt.UserRole) if item is not None else ""

    def _refresh_row(self, item, size=None):
        path = self._row_path(item)
        name = os.path.basename(path)
        if size is None:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
        bits = []
        n = self._counts.get(path)
        if n is not None:
            bits.append("unreadable" if n < 0 else plural(n, "page"))
        bits.append(f"{size / 1048576:.1f} MB" if size >= 104858 else f"{max(size // 1024, 1)} KB")
        m = self._matches.get(path)
        if m is not None and self._search_text:
            if m.get("error"):
                bits.append("search failed")
            elif not m["has_text"]:
                bits.append("no text")
            elif m["hits"]:
                bits.append(f"{m['hits']} {'match' if m['hits'] == 1 else 'matches'} on {plural(len(m['pages']), 'page')}")
        state = self._file_state.get(path)
        mark = {"saved": "✓ ", "skipped": "– ", "new": "+ ", "added": "+ ",
                "changed": "! ", "gone": "! "}.get(state, "")
        item.setText(f"{mark}{name}\n{' · '.join(bits)}")
        item.setToolTip(path)

    def _on_info(self, token, path, n):
        if token != self._token:
            return
        self._counts[path] = n
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if self._row_path(it) == path:
                self._refresh_row(it)
                break

    def _on_file_changed(self, current, previous):
        if self._loading_row or current is None:
            return
        if self._busy:                     # a save is writing: stay put
            self._loading_row = True
            self.file_list.setCurrentItem(previous)
            self._loading_row = False
            return
        if self._is_dirty():
            prev_name = os.path.basename(self._cur_path)
            ans = self._ask_save(prev_name)
            if ans == QMessageBox.Cancel:
                self._loading_row = True
                self.file_list.setCurrentItem(previous)
                self._loading_row = False
                return
            if ans == QMessageBox.Save:
                self._loading_row = True
                self.file_list.setCurrentItem(previous)
                self._loading_row = False
                self._start_save(advance_to=self._row_path(current))
                return
        self._open_file(self._row_path(current))

    def _ask_save(self, name):
        self._launch_guard = True            # launches that arrive while this prompt is open are queued, not shown
        try:
            return Msg.question(
                self, "Unsaved Changes",
                f"You changed the pages of {name} but have not saved it.\n\n"
                "Save it now (into the Reorganized folder), throw the changes away, or stay here?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        finally:
            self._launch_guard = False

    def _confirm_leave(self, where):
        ans = self._ask_save(os.path.basename(self._cur_path))
        if ans == QMessageBox.Cancel:
            return False
        if ans == QMessageBox.Save:
            self._set_status("Save the PDF first (Save & Next), then change folders.", "error")
            return False
        return True

    # ------------------------------------------------------------ keeping the list live
    def _rebuild_row_index(self):
        self._row_of = {}
        for i in range(self.file_list.count()):
            self._row_of.setdefault(self._row_path(self.file_list.item(i)), i)

    def _row_for(self, path):
        row = self._row_of.get(path)
        it = self.file_list.item(row) if row is not None else None
        return (row, it) if it is not None and self._row_path(it) == path else (None, None)

    def _insert_rows(self, rows):
        """rows: [(path, (size, mtime_ns), state or None)] -> inserted in natural order.
        Signals are blocked so the open PDF and the selection are not disturbed."""
        import bisect
        keys = [natural_name_key(os.path.basename(self._row_path(self.file_list.item(i))))
                for i in range(self.file_list.count())]
        self.file_list.blockSignals(True)
        try:
            for path, sig, state in rows:
                key = natural_name_key(os.path.basename(path))
                pos = bisect.bisect_right(keys, key)
                keys.insert(pos, key)
                it = QListWidgetItem()
                it.setData(Qt.UserRole, path)
                self.file_list.insertItem(pos, it)
                self._row_sig[path] = sig
                if state:
                    self._file_state[path] = state
                self._refresh_row(it, sig[0])
        finally:
            self.file_list.blockSignals(False)
        self._rebuild_row_index()
        if self._search_text and self.only_matches.isChecked():
            self._apply_filter()

    def _note_output_file(self, path, pages, state) -> bool:
        """A PDF this panel just wrote. When it landed in the folder on screen, show it
        in the list (or refresh its row, if it replaced a file that is already listed)."""
        loaded = self._folder_loaded
        if not loaded or not path or not path.lower().endswith(".pdf"):
            return False
        if os.path.normcase(os.path.abspath(os.path.dirname(path))) != \
                os.path.normcase(os.path.abspath(loaded)):
            return False
        lp = os.path.join(loaded, os.path.basename(path))   # spelled as the listing spells it
        try:
            st = os.stat(path)
        except OSError:
            return False
        sig = (st.st_size, st.st_mtime_ns)
        self._counts[lp] = pages
        _row, it = self._row_for(lp)
        if it is None:
            self._insert_rows([(lp, sig, state)])
        else:
            self._row_sig[lp] = sig
            self._refresh_row(it, st.st_size)
        return True

    def refresh_list(self):
        """Look for PDFs added, removed or changed in the folder on screen (F5)."""
        self._sync_list(force=True)

    def _sync_list(self, force=False):
        if self._shutting_down or self._busy or not self._folder_loaded or self._sync_running:
            return
        if not os.path.isdir(self._folder_loaded):
            if force:
                self._set_status("That folder is not available right now.", "error")
            return
        now = time.monotonic()
        if not force and now - self._last_sync < self.SYNC_MIN_GAP:
            return
        self._last_sync = now
        self._sync_running = True
        w = self._track(_ListWorker(self._folder_loaded, self._token))
        w.listed.connect(self._on_listed)
        w.finished.connect(self._sync_finished)
        w.start()

    def _sync_finished(self):
        self._sync_running = False

    def _on_listed(self, token, folder, listing):
        if token != self._token or folder != self._folder_loaded or self._busy or self._shutting_down:
            return
        self._apply_listing(listing)

    def _apply_listing(self, listing):
        seen = {d["path"] for d in listing}
        added, changed = [], []
        for d in listing:
            sig = (d["size"], d.get("mtime_ns"))
            old = self._row_sig.get(d["path"])
            if old is None:
                added.append((d["path"], sig, "added"))
            elif old != sig:
                changed.append((d["path"], sig))
        gone = [p for p in list(self._row_sig) if p not in seen
                and not (p == self._cur_path and self._file_state.get(p) == "gone")]   # already reported
        if not (added or changed or gone):
            return
        notes, rescan = [], []
        removed_n = 0
        if gone:
            self.file_list.blockSignals(True)
            try:
                for path in gone:
                    if path == self._cur_path:          # open in the grid: keep it, say so
                        self._file_state[path] = "gone"
                        self._reader_note_stale()
                        _r, it = self._row_for(path)
                        if it is not None:
                            self._refresh_row(it)
                        notes.append(f"{os.path.basename(path)} is no longer in the folder (it is still open here)")
                        continue
                    row, it = self._row_for(path)
                    if it is not None:
                        self.file_list.takeItem(row)
                        self._rebuild_row_index()
                        removed_n += 1
                    for d in (self._row_sig, self._counts, self._matches, self._file_state):
                        d.pop(path, None)
            finally:
                self.file_list.blockSignals(False)
        if removed_n:
            notes.append(f"{plural(removed_n, 'PDF')} removed")
        for path, sig in changed:
            self._row_sig[path] = sig
            self._counts.pop(path, None)
            self._matches.pop(path, None)
            if path == self._cur_path and self._pages:
                self._file_state[path] = "changed"
                self._reader_note_stale()
                notes.append(f"{os.path.basename(path)} changed on disk since you opened it - "
                             "saving will be refused; click it again to reload")
            _r, it = self._row_for(path)
            if it is not None:
                self._refresh_row(it, sig[0])
            rescan.append(path)
        if added:
            self._insert_rows(added)
            for path, _sig, _st in added:
                rescan.append(path)
            notes.append(f"{plural(len(added), 'new PDF')}")
        if rescan:
            w = self._track(_InfoWorker(rescan, self._token))
            w.info.connect(self._on_info)
            w.start()
            if self._search_text:
                w2 = self._track(_SearchWorker(rescan, self._search_text, self._search_gen))
                w2.result.connect(self._on_search_result)
                w2.start()
        if not self.file_list.count():
            self.info_label.setText("No PDFs found. Subfolders are not searched.")
        if notes:
            self._set_status("Folder updated: " + "; ".join(notes) + ".", "")

    def _arm_timer(self):
        want = self.isVisible() and setting("organizer_auto_refresh") == "timer"
        if want and not self._sync_timer.isActive():
            self._sync_timer.start()
        elif not want and self._sync_timer.isActive():
            self._sync_timer.stop()

    def _on_sync_timer(self):
        if setting("organizer_auto_refresh") == "timer" and self.isVisible():
            self._sync_list()
        else:
            self._arm_timer()

    def _on_app_state(self, state):
        if state == Qt.ApplicationActive and self.isVisible():
            self._arm_timer()
            if setting("organizer_auto_refresh") in ("focus", "timer"):
                self._sync_list()

    def showEvent(self, event):
        super().showEvent(event)
        self._arm_timer()
        if setting("organizer_auto_refresh") in ("focus", "timer"):
            self._sync_list()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._sync_timer.stop()

    # ------------------------------------------------------------ opening a PDF
    def _clear_grid(self, keep_reader=False):
        if self.reader is not None and self._reader_active():
            self.reader.close_document("Opening the next PDF…" if keep_reader else "")
            if not keep_reader:
                self.stack.setCurrentIndex(0)
        self.grid.clear()
        self._clip_epoch += 1
        self._pages = []
        self._undo = []
        self._texts = {}
        self._text_note_shown = False
        self._all = {}
        self._total = 0
        self._thumbs = {}
        self._hi = OrderedDict()
        self._hi_w = 0
        self._hi_epoch += 1
        self._cur_path = ""
        self._cur_stamp = None
        self._set_tools_enabled(False)

    def _open_file(self, path: str):
        self._cancel_background_thumbs()
        if self._reader_want and self._reader_want[0] != path:
            self._reader_want = None
        was_reading = self._reader_active()
        self._clear_grid(keep_reader=was_reading and bool(path))
        if not path:
            return
        if was_reading:                    # Save & Next / Skip keep you in the reader
            self._reader_want = (path, time.monotonic(), 0)
        try:
            self._cur_stamp = source_stamp(path)
        except OSError as e:
            self._reader_want = None
            self._leave_reader_view()
            self._set_status(describe_lock_failure(path, e), "error")
            return
        self._cur_path = path
        k = self._launch_index(path)
        if k >= 0:                           # opened by hand from the folder list: it is no longer "waiting"
            self._launch_pos = k
            self._launch_state[self._launch_q[k]] = "viewed"
            self._refresh_launch_ui()
        self.info_label.setText("Loading pages…")
        self._set_status(f"Opening {os.path.basename(path)}…", "busy")
        w = self._track(_ThumbWorker(path, self._thumb_width() * 2, self._thumb_token))
        w.total.connect(self._on_total)
        w.thumb.connect(self._on_thumb)
        w.failed.connect(self._on_thumb_failed)
        w.start()

    def _cancel_background_thumbs(self):
        # Only the page renderer is replaced when the operator clicks another
        # file; a running search or page-count pass carries on.
        self._thumb_token += 1
        self._preview_epoch += 1
        for w in self._bg:
            if isinstance(w, (_ThumbWorker, _PageImageWorker, _PreviewWorker)) and not isinstance(w, _BWorker):
                w.cancel()
        if self._preview_path:
            self._drop_preview()

    def _on_total(self, token, n):
        if token != self._thumb_token:
            return
        match = set(self._matches.get(self._cur_path, {}).get("pages", [])) if self._search_text else set()
        self._pages = [{"idx": i, "rot": 0, "blank": False,
                        "match": i in match} for i in range(n)]
        self._all = {p["idx"]: p for p in self._pages}
        self._total = n
        self._undo = []
        self._texts = {}
        self._text_note_shown = False
        self._rebuild_grid()
        self._set_tools_enabled(True)
        self._set_status(f"{getattr(self, '_carry_note', '')}{os.path.basename(self._cur_path)}: {plural(n, 'page')}.", "")
        self._carry_note = ""
        if match:
            first = min(match)
            self.grid.scrollToItem(self.grid.item(first))
        self._update_info()
        self._maybe_open_reader()

    def _on_thumb(self, token, index, image, blank):
        if token != self._thumb_token:
            return
        self._thumbs[index] = image
        if self._hi_target():
            self._zoom_timer_start()
        page = self._all.get(index)
        if page is not None:
            page["blank"] = blank
        self._refresh_items(only_idx=index)

    def _on_thumb_failed(self, token, message):
        if token != self._thumb_token:
            return
        failed_path = self._cur_path
        self._set_status(f"{os.path.basename(self._cur_path)} could not be shown: {message}", "error")
        self.info_label.setText(message)
        self._reader_want = None
        self._clear_grid()
        k = self._launch_index(failed_path)
        if k >= 0 and self._launch_state.get(self._launch_q[k]) == "viewed":
            self._launch_state[self._launch_q[k]] = "failed"     # a PDF from the list that will not open: say so, move on
            self._launch_fail = (self._launch_q[k], message)
            self._refresh_launch_ui()
            QTimer.singleShot(0, self._launch_after_failure)

    def _launch_after_failure(self):
        """Skip a listed PDF that could not be opened (too large, not a PDF, damaged, a bomb) and
        show the next waiting one in the direction the operator was going, with the reason."""
        fail, self._launch_fail = getattr(self, "_launch_fail", None), None
        if not fail or self._busy or self._launch_guard:
            return
        path, why = fail
        if self._cur_path and self._qkey(self._cur_path) != self._qkey(path):
            return                                               # the operator already went elsewhere
        step = getattr(self, "_launch_dir", 1)
        j = self._launch_pos + step
        while 0 <= j < len(self._launch_q) and not (
                self._launch_state.get(self._launch_q[j]) == "waiting" and os.path.isfile(self._launch_q[j])):
            j += step
        if not 0 <= j < len(self._launch_q):
            return                                               # nothing left that way: the error stays on screen
        self._carry_note = getattr(self, "_carry_note", "") + f"{os.path.basename(path)} was skipped: {why} "
        if not self._launch_goto(j):
            self._carry_note = ""

    # ------------------------------------------------------------ grid <-> plan
    def _is_dirty(self) -> bool:
        if not self._pages:
            return False
        if self._has_pasted() or self._has_texts():
            return True
        return not plan_is_unchanged(self._plan(), self._total)

    @staticmethod
    def _pasted(page) -> bool:
        return "src_path" in page

    def _has_pasted(self) -> bool:
        return any(self._pasted(p) for p in self._pages if not p.get("cut"))

    # ---- pages that were Cut stay in the grid, faded, until they are pasted (moved) or the cut is cancelled.
    # They are NOT part of what is saved. Everything that builds the saved file reads the live pages only.
    def _live_pages(self):
        return [p for p in self._pages if not p.get("cut")]

    def _live_numbers(self):
        """The place each page has in the file being built (cut pages do not take a number)."""
        out, n = [], 0
        for p in self._pages:
            out.append(n)
            if not p.get("cut"):
                n += 1
        return out

    def _cut_pending(self) -> bool:
        return any(p.get("cut") for p in self._pages)

    def _would_empty(self, rows) -> bool:
        """True if removing these grid rows would leave no page to save."""
        gone = set(rows)
        return not any(not p.get("cut") for r, p in enumerate(self._pages) if r not in gone)

    def _cancel_cut(self):
        """Put cut pages back as ordinary pages (what Explorer does when you Copy after a Cut)."""
        if not self._cut_pending():
            return
        for p in self._pages:
            p.pop("cut", None)
        _PAGE_CLIP.cut = None
        self._rebuild_grid(self._selected_idx())

    def _commit_cut(self):
        """Take cut pages out for good (one Undo brings them back). Used where the faded pages would
        confuse the reader, whose pages are exactly the ones that will be saved."""
        if not self._cut_pending():
            return
        keep = {i for i in self._selected_idx()}
        self._pages = self._live_pages()
        _PAGE_CLIP.cut = None
        for p in self._all.values():
            p.pop("cut", None)
        self._rebuild_grid(keep)

    def _plan(self):
        """What will be saved. [(page, rotation)] for the open file alone; once pages from another
        file (or copies) are in, [(source, page, rotation)] with source 0 = the open file."""
        live = self._live_pages()
        if not self._has_pasted():
            return [(p["idx"], p["rot"]) for p in live]
        ids, plan = {}, []
        here = self._qkey(self._cur_path)
        for p in live:
            if self._pasted(p):
                k = self._qkey(p["src_path"])
                if k == here:
                    plan.append((0, p["sidx"], p["rot"]))
                else:
                    plan.append((ids.setdefault(k, len(ids) + 1), p["sidx"], p["rot"]))
            else:
                plan.append((0, p["idx"], p["rot"]))
        return plan

    def _extra_sources(self):
        """([paths], [stamps]) of the other files the plan reads, numbered 1.. in the order _plan() numbers them."""
        paths, stamps, seen = [], [], set()
        here = self._qkey(self._cur_path)
        for p in self._live_pages():
            if self._pasted(p):
                k = self._qkey(p["src_path"])
                if k != here and k not in seen:
                    seen.add(k)
                    paths.append(p["src_path"])
                    stamps.append(p.get("stamp"))
        return paths, stamps

    def _cell(self):
        w = self._thumb_width()
        return QSize(w, int(w * 1.32))

    # ------------------------------------------------------------ zoom (Ctrl + mouse wheel)
    @staticmethod
    def _load_zoom():
        try:
            v = load_ui_state().get("organizer_zoom")
        except Exception:
            return None
        if isinstance(v, int) and not isinstance(v, bool) and ORG_ZOOM_MIN <= v <= ORG_ZOOM_MAX:
            return v
        return None

    def _grid_w(self) -> int:
        return self._zoom_w or self._thumb_width()

    def _grid_cell(self):
        w = self._grid_w()
        return QSize(w, int(w * 1.32))

    def _lazy(self) -> bool:
        """Far zoomed in, only the pages near the window keep a full-size picture (memory stays small)."""
        return self._grid_w() > ORG_LAZY_W

    def _zoom_sync_ui(self):
        w = self._grid_w()
        base = max(1, self._thumb_width())
        self.zoom_label.setText(f"Pictures {w} px wide ({round(100 * w / base)}%)")
        self.zoom_out_btn.setEnabled(w > ORG_ZOOM_MIN)
        self.zoom_in_btn.setEnabled(w < ORG_ZOOM_MAX)
        self.zoom_reset_btn.setEnabled(self._zoom_w is not None)

    def _zoom_in_clicked(self):
        self.zoom_by(1)

    def _zoom_out_clicked(self):
        self.zoom_by(-1)

    def zoom_by(self, steps: int):
        w = self._grid_w()
        new = int(round(w * (ORG_ZOOM_STEP ** int(steps))))
        if new == w:
            new += 1 if steps > 0 else -1
        self.set_zoom(new)

    def zoom_reset(self):
        self.set_zoom(None)

    def set_zoom(self, w):
        if w is not None:
            w = max(ORG_ZOOM_MIN, min(ORG_ZOOM_MAX, int(w)))
            if w == self._thumb_width():
                w = None
        if w == self._zoom_w:
            return
        top = self._top_row()
        self._zoom_w = w
        try:
            update_ui_state(organizer_zoom=w)
        except Exception:
            pass
        self._hi_epoch += 1                      # a sharper picture drawn for the old size is out of date
        self._cancel_hi()
        self._hi_w = 0
        self._hi.clear()
        self.grid.setIconSize(self._grid_cell())
        self._live = self._window_for(top)
        self._refresh_items()
        if self.grid.count():
            self.grid.scrollToItem(self.grid.item(min(top, self.grid.count() - 1)), QAbstractItemView.PositionAtTop)
        self._zoom_sync_ui()
        self._set_status(f"Page pictures are {self._grid_w()} px wide. Ctrl + mouse wheel changes this; Ctrl+0 resets it.", "")
        self._zoom_timer_start()

    def _top_row(self) -> int:
        rect = self.grid.viewport().rect()
        for r in range(self.grid.count()):
            it = self.grid.item(r)
            if self.grid.visualItemRect(it).bottom() > rect.top():
                return r
        return 0

    def _visible_rows(self):
        rect = self.grid.viewport().rect()
        rows = []
        for r in range(self.grid.count()):
            vr = self.grid.visualItemRect(self.grid.item(r))
            if vr.bottom() < rect.top():
                continue
            if vr.top() > rect.bottom():
                break
            rows.append(r)
        return rows

    def _window_for(self, top: int):
        n = self.grid.count()
        lo = max(0, top - ORG_LIVE_ROWS // 2)
        return (lo, min(max(n - 1, 0), lo + ORG_LIVE_ROWS * 2))

    def _zoom_timer_start(self, *_a):
        if not self._shutting_down:
            self._zoom_timer.start()

    def _zoom_settle(self):
        """After the wheel stops or the grid scrolls: refresh which pages hold full-size pictures and ask for
        sharper ones for the pages in view."""
        if self._shutting_down or not self.grid.count():
            return
        vis = self._visible_rows()
        if self._lazy() and vis:
            lo = max(0, vis[0] - ORG_LIVE_ROWS // 2)
            hi = min(self.grid.count() - 1, vis[-1] + ORG_LIVE_ROWS // 2)
            old = self._live
            self._live = (lo, hi)
            for r in set(range(old[0], old[1] + 1)) ^ set(range(lo, hi + 1)):
                self._refresh_grid_row(r)
        self._request_hi(vis)

    def _refresh_grid_row(self, pos):
        if not 0 <= pos < len(self._pages):
            return
        it = self.grid.item(pos)
        if it is not None:
            it.setIcon(self._icon_for(pos))

    def _icon_for(self, pos):
        if self._lazy() and not (self._live[0] <= pos <= self._live[1]):
            return self._placeholder_icon()
        return self._page_icon(self._pages[pos])

    def _placeholder_icon(self):
        cell = self._grid_cell()
        key = (cell.width(), cell.height(), theme_color("row_alt"))
        cached = getattr(self, "_ph", None)
        if cached and cached[0] == key:
            return cached[1]
        canvas = QPixmap(cell)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.fillRect(canvas.rect().adjusted(2, 2, -2, -2), QColor(theme_color("row_alt")))
        painter.end()
        icon = QIcon(canvas)
        self._ph = (key, icon)
        return icon

    def _cancel_hi(self):
        for w in self._bg:
            if isinstance(w, _PageImageWorker) and not isinstance(w, _BWorker):
                w.cancel()

    def _hi_target(self) -> int:
        """Width to draw sharper pictures at, or 0 when the ones already held are sharp enough."""
        cell_w = self._grid_w()
        if cell_w <= self._thumb_width() * 2:
            return 0
        return min(ORG_HI_MAX_W, int(cell_w * 1.4))

    def _request_hi(self, rows):
        target = self._hi_target()
        if not target or not self._cur_path or self._shutting_down:
            return
        if self._hi_w and abs(self._hi_w - target) > target * 0.15:
            self._hi.clear()                      # drawn for a different size: start over
            self._hi_w = 0
        jobs = []
        for r in rows[:ORG_HI_MAX_PAGES]:
            page = self._pages[r] if r < len(self._pages) else None
            if page is None or self._pasted(page) or page["idx"] in self._hi:
                continue
            jobs.append((page["idx"], self._cur_path, page["idx"]))
        if not jobs:
            return
        self._hi_epoch += 1
        self._cancel_hi()
        self._hi_w = target
        w = self._track(_PageImageWorker(jobs, target, self._hi_epoch))
        w.image.connect(self._on_hi_image)
        w.start()

    def _on_hi_image(self, epoch, idx, img):
        if epoch != self._hi_epoch or self._shutting_down or not self._grid_w() > self._thumb_width() * 2:
            return
        self._hi[idx] = img
        self._hi.move_to_end(idx)
        while len(self._hi) > ORG_HI_MAX_PAGES:
            self._hi.popitem(last=False)
        self._refresh_items(only_idx=idx)

    def _page_icon(self, page, strip=False):
        cell = self._cell() if strip else self._grid_cell()
        canvas = QPixmap(cell)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        img = self._hi.get(page["idx"]) if not self._pasted(page) else None
        if img is None:
            img = self._thumbs.get(page["idx"])
        if img is None:
            painter.fillRect(canvas.rect().adjusted(2, 2, -2, -2), QColor(theme_color("row_alt")))
        else:
            pm = QPixmap.fromImage(img)
            if page["rot"]:
                pm = pm.transformed(QTransform().rotate(page["rot"]), Qt.SmoothTransformation)
            pm = pm.scaled(cell, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            px, py = (cell.width() - pm.width()) // 2, (cell.height() - pm.height()) // 2
            painter.drawPixmap(px, py, pm)
            edge = QPen(QColor(theme_color("border")))
            edge.setWidth(1)
            painter.setPen(edge)
            painter.drawRect(px, py, pm.width() - 1, pm.height() - 1)
        if page["match"]:
            pen = QPen(QColor("#e0a800"))
            pen.setWidth(5)
            painter.setPen(pen)
            painter.drawRect(canvas.rect().adjusted(2, 2, -3, -3))
        painter.end()
        if page.get("cut") and not strip:
            faded = QPixmap(cell)
            faded.fill(Qt.transparent)
            fp = QPainter(faded)
            fp.setOpacity(0.32)
            fp.drawPixmap(0, 0, canvas)
            fp.setOpacity(1.0)
            dash = QPen(QColor(theme_color("accent")))
            dash.setWidth(2)
            dash.setStyle(Qt.DashLine)
            fp.setPen(dash)
            fp.drawRect(faded.rect().adjusted(2, 2, -3, -3))
            fp.end()
            canvas = faded
        return QIcon(canvas)

    def _item_label(self, page, position, number=None):
        # The label is the page's PLACE in the file being built, so the pages
        # always read 1, 2, 3 ... in order; "was page N" says where it came from.
        if page.get("cut"):
            return f"Cut (was page {page['idx'] + 1})\npaste to move it" if not self._pasted(page) else \
                "Cut\npaste to move it"
        position = position if number is None else number
        parts = [f"Page {position + 1}"]
        if self._pasted(page):
            parts.append(f"from {os.path.basename(page['src_path'])}, page {page['sidx'] + 1}")
        elif page["idx"] != position:
            parts.append(f"was page {page['idx'] + 1}")
        if page["rot"]:
            parts.append(f"turned {page['rot']}°")
        if page["blank"]:
            parts.append("looks blank")
        if page["match"]:
            parts.append("match")
        return "\n".join([parts[0], ", ".join(parts[1:])] if len(parts) > 1 else [parts[0], " "])

    def _rebuild_grid(self, keep_selected=None):
        self.grid.blockSignals(True)
        self.grid.clear()
        self.grid.setIconSize(self._grid_cell())
        self._live = (0, ORG_LIVE_ROWS * 2)
        nums = self._live_numbers()
        for pos, page in enumerate(self._pages):
            it = QListWidgetItem(self._icon_for(pos), self._item_label(page, pos, nums[pos]))
            it.setData(self._ROLE_IDX, page["idx"])
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.grid.addItem(it)
            if keep_selected and page["idx"] in keep_selected:
                it.setSelected(True)
        self.grid.blockSignals(False)
        self._update_info()
        self._reader_pages_changed()

    def _refresh_items(self, only_idx=None):
        nums = self._live_numbers()
        for pos, page in enumerate(self._pages):
            if only_idx is not None and page["idx"] != only_idx:
                continue
            it = self.grid.item(pos)
            if it is not None:
                it.setIcon(self._icon_for(pos))
                it.setText(self._item_label(page, pos, nums[pos]))
        if self.reader is not None and self._reader_active():
            self.reader.refresh_icons(only_idx)

    def _sync_from_grid(self):
        """After a drag-drop: adopt the order the grid now shows."""
        order = [self.grid.item(r).data(self._ROLE_IDX) for r in range(self.grid.count())]
        by_idx = {p["idx"]: p for p in self._pages}
        if sorted(order) == sorted(by_idx):
            self._pages = [by_idx[i] for i in order]
            self._update_info()
        else:                              # the grid and the plan disagree: the plan wins
            self._rebuild_grid()

    def _selected_positions(self):
        return sorted(self.grid.row(i) for i in self.grid.selectedItems())

    def _selected_idx(self):
        return {self._pages[r]["idx"] for r in self._selected_positions()}

    def _need_selection(self) -> list:
        pos = self._selected_positions()
        if not pos:
            self._set_status(
                "Click one or more pages first (Ctrl or Shift to pick several)." if self._cur_path else
                "No PDF is open. Choose a folder, then click a PDF in the list.", "error")
        return pos

    def rotate_left(self):
        self._rotate(-90)

    def rotate_half_turn(self):
        self._rotate(180)

    def move_to_start(self):
        self._move_selection_to(0)

    def move_to_end(self):
        self._move_selection_to(len(self._pages))

    def _on_pages_dropped(self, rows, target):
        """Drag and drop: `rows` are grid positions, `target` the insertion
        index in the grid as it looked when the drop happened."""
        if self._busy or not self._pages:
            return
        rows = sorted({r for r in rows if 0 <= r < len(self._pages)})
        if rows:
            self._move_selection_to(max(0, min(target, len(self._pages))), rows)

    def _move_selection_to(self, target, rows=None):
        rows = self._need_selection() if rows is None else rows
        if not rows or self._busy:
            return
        keep = {self._pages[r]["idx"] for r in rows}
        moving = [self._pages[r] for r in rows]
        before = sum(1 for r in rows if r < target)          # removed ahead of the gap
        rest = [p for r, p in enumerate(self._pages) if r not in set(rows)]
        at = max(0, min(target - before, len(rest)))
        new = rest[:at] + moving + rest[at:]
        if [p["idx"] for p in new] == [p["idx"] for p in self._pages]:
            return                                           # dropped where it already was
        self._push_undo()
        self._pages = new
        self._rebuild_grid(keep)
        first = min(self.grid.row(i) for i in self.grid.selectedItems()) if self.grid.selectedItems() else 0
        self.grid.scrollToItem(self.grid.item(first))

    def rotate_right(self):
        self._rotate(90)

    def _rotate(self, delta):
        pos = self._need_selection()
        if not pos:
            return
        keep = self._selected_idx()
        self._push_undo()
        for r in pos:
            self._pages[r]["rot"] = (self._pages[r]["rot"] + delta) % 360
        self._rebuild_grid(keep)

    def delete_pages(self):
        """Take the selected pages out of the PDF being built. Undo (or Reset)
        brings them back; the file on disk is not touched until Save."""
        pos = self._need_selection()
        if not pos or self._busy:
            return
        if self._would_empty(pos):
            self._set_status("A PDF needs at least one page. Leave at least one page in.", "error")
            return
        self._push_undo()
        gone = set(pos)
        self._pages = [p for r, p in enumerate(self._pages) if r not in gone]
        self._rebuild_grid()
        first = min(pos[0], self.grid.count() - 1)
        self.grid.scrollToItem(self.grid.item(first))
        self._set_status(f"Deleted {plural(len(pos), 'page')}. Undo brings "
                         f"{'it' if len(pos) == 1 else 'them'} back.", "")

    # ------------------------------------------------------------ cut / copy / paste
    def _clip_entries(self, rows):
        cell = self._cell()
        out = []
        for r in rows:
            p = self._pages[r]
            if self._pasted(p):
                path, sidx, stamp = p["src_path"], p["sidx"], p.get("stamp")
            else:
                path, sidx, stamp = self._cur_path, p["idx"], self._cur_stamp
            img = self._thumbs.get(p["idx"])
            if img is not None:
                img = img.scaled(cell, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            out.append({"path": path, "sidx": sidx, "stamp": stamp, "rot": p["rot"], "img": img})
        return out

    def copy_pages(self):
        """Ctrl+C: remember the selected pages (with where they came from) so they can be pasted
        into this PDF or any other. Nothing in this PDF changes."""
        if self._busy or not self._pages:
            return
        self._cancel_cut()                                  # a Copy after a Cut cancels the cut, as in Explorer
        pos = self._need_selection()
        if not pos:
            return
        _PAGE_CLIP.set(self._clip_entries(pos))
        n = len(_PAGE_CLIP)
        cut = f" Only the first {CLIP_MAX_PAGES} fit." if len(pos) > CLIP_MAX_PAGES else ""
        self._set_status(f"Copied {plural(n, 'page')}.{cut} Click where they should go and press Ctrl+V.", "")
        self._update_info()

    def cut_pages(self):
        """Ctrl+X: mark the selected pages as cut. They stay where they are, faded, so it is clear what is
        being moved; Ctrl+V moves them to the new place (or into another PDF). They are left out of the
        saved file. Undo, or a Copy, cancels the cut."""
        if self._busy or not self._pages:
            return
        self._cancel_cut()                                  # a new Cut replaces the old one
        pos = self._need_selection()
        if not pos:
            return
        if self._would_empty(pos):
            self._set_status("A PDF needs at least one page. Leave at least one page in.", "error")
            return
        if self._reader_active() or self._preview_path:     # the reader shows saved pages only: remove now
            _PAGE_CLIP.set(self._clip_entries(pos))
            n = len(_PAGE_CLIP)
            self.delete_pages()
            self._set_status(f"Cut {plural(n, 'page')}. Open the PDF they belong in and press Ctrl+V "
                             "(Undo puts them back here). They are only gone from this PDF once you save it.", "")
            self._update_info()
            return
        take = pos[:CLIP_MAX_PAGES]
        entries = self._clip_entries(take)
        self._push_undo()
        tokens = []
        for r in take:
            self._pages[r]["cut"] = True
            tokens.append(self._pages[r]["idx"])
        _PAGE_CLIP.set(entries, cut={"owner": id(self), "tokens": tokens})
        n = len(take)
        more = f" Only the first {CLIP_MAX_PAGES} were cut." if len(pos) > CLIP_MAX_PAGES else ""
        keep = self._selected_idx()
        self._rebuild_grid(keep)
        self._set_status(f"Cut {plural(n, 'page')}.{more} Click where they go and press Ctrl+V (they stay faded "
                         "until then, and are left out if you save first). Undo cancels.", "")
        self._update_info()

    def _move_cut_pages(self, before=False) -> bool:
        """Ctrl+V after a Cut in this same PDF: the cut pages themselves move to the new place."""
        cut = _PAGE_CLIP.cut
        if not cut or cut.get("owner") != id(self):
            return False
        toks = set(cut.get("tokens", ()))
        moved = [p for p in self._pages if p.get("cut") and p["idx"] in toks]
        if not moved:
            return False
        pos = self._selected_positions()
        at = (pos[0] if before else pos[-1] + 1) if pos else len(self._pages)
        removed_before = sum(1 for r, p in enumerate(self._pages) if r < at and p.get("cut") and p["idx"] in toks)
        ids = {id(p) for p in moved}
        rest = [p for p in self._pages if id(p) not in ids]
        at2 = max(0, min(at - removed_before, len(rest)))
        self._push_undo()
        for p in moved:
            p.pop("cut", None)
        self._pages = rest[:at2] + moved + rest[at2:]
        _PAGE_CLIP.cut = None
        self._rebuild_grid({p["idx"] for p in moved})
        self.grid.scrollToItem(self.grid.item(at2))
        num = sum(1 for p in self._pages[:at2] if not p.get("cut"))
        where = f"after page {num}" if num else "to the start"
        self._set_status(f"Moved {plural(len(moved), 'page')} {where}. They are saved when you press "
                         "Save & Next PDF. Undo puts them back.", "")
        return True

    def _paste_source_problem(self, path, stamp):
        """Why pages from `path` cannot be pasted, or None."""
        name = os.path.basename(path)
        if not os.path.isfile(path):
            return f"{name} is no longer there"
        try:
            if stamp is not None and tuple(source_stamp(path)) != tuple(stamp):
                return f"{name} changed after you copied from it"
        except OSError as e:
            return describe_lock_failure(path, e)
        problem = pdf_expansion_problem(path)
        return problem or None

    def paste_pages(self, before=False, entries=None, at=None):
        """Ctrl+V puts the clipboard's pages after the selected page (Ctrl+Shift+V before it; at the
        end when nothing is selected). They are saved with this PDF; their source files are not changed.
        `entries` / `at` let the B pane put its own pages in, at a given place, without the clipboard."""
        if self._busy:
            return
        if not self._pages:
            self._set_status("Open a PDF first, then paste.", "error")
            return
        if entries is None:
            files = clipboard_pdf_paths()                     # PDF files copied in Explorer: paste all their pages
            if files:
                self._cancel_cut()
                self.insert_pdf_files(files, before=before)
                return
            if self._move_cut_pages(before):
                return
        items = _PAGE_CLIP.items if entries is None else entries
        if not items:
            self._set_status("Nothing to paste. Select pages and press Ctrl+C (copy) or Ctrl+X (cut) first.", "error")
            return
        pos = self._selected_positions()
        explicit = at is not None
        if explicit:
            at = max(0, min(int(at), len(self._pages)))
        else:
            at = (pos[0] if before else pos[-1] + 1) if pos else len(self._pages)
        verdict, skipped = {}, {}
        usable = []
        for it in items:
            if it["path"] not in verdict:
                verdict[it["path"]] = self._paste_source_problem(it["path"], it["stamp"])
            if verdict[it["path"]] is None:
                usable.append(it)
            else:
                skipped[it["path"]] = skipped.get(it["path"], 0) + 1
        notes = "; ".join(f"{plural(n, 'page')} from {verdict[pth]} were skipped" for pth, n in skipped.items())
        if not usable:
            self._set_status(f"Nothing was pasted: {notes}.", "error")
            return
        if len(self._pages) + len(usable) > PASTE_PAGES_MAX:
            self._set_status(f"A PDF built here can have up to {PASTE_PAGES_MAX} pages.", "error")
            return
        self._push_undo()
        new, jobs = [], []
        for it in usable:
            tok = self._next_token
            self._next_token += 1
            page = {"idx": tok, "rot": it["rot"], "blank": False, "match": False,
                    "src_path": it["path"], "sidx": it["sidx"], "stamp": it["stamp"]}
            self._all[tok] = page
            self._thumbs[tok] = it["img"]
            if it["img"] is None:
                jobs.append((tok, it["path"], it["sidx"]))
            new.append(page)
        self._pages[at:at] = new
        self._rebuild_grid({p["idx"] for p in new})
        self.grid.scrollToItem(self.grid.item(at))
        if jobs and not self._shutting_down:
            w = self._track(_PageImageWorker(jobs, self._thumb_width() * 2, self._clip_epoch))
            w.image.connect(self._on_page_image)
            w.start()
        where = (f"before page {at + 1}" if before and pos else f"after page {at}" if at else "at the start")
        self._set_status(f"Pasted {plural(len(new), 'page')} {where}." + (f" {notes}." if notes else "")
                         + " They are saved when you press Save & Next PDF. Undo takes them out.", "error" if notes else "")

    def insert_pdf_files(self, paths, at=None, before=False):
        """Put EVERY page of the given PDF files into this PDF: at the gap `at` when given (a drop), else
        after the selected page (before it with `before`), else at the end. The files are only read.
        Bounded like Copy: at most CLIP_MAX_PAGES pages per insert."""
        if self._busy:
            return
        if not self._pages:
            self._set_status("Open a PDF first, then insert pages into it.", "error")
            return
        info = classify_drop([p for p in paths if isinstance(p, str)])
        notes = [f"{name} {why}" for name, why in info["skipped"]]
        entries = []
        for path in info["pdfs"]:
            path = os.path.abspath(path)
            name = os.path.basename(path)
            room = CLIP_MAX_PAGES - len(entries)
            if room <= 0:
                notes.append(f"{name} was left out (an insert takes up to {CLIP_MAX_PAGES} pages)")
                continue
            try:
                require_safe_to_expand(path)
                n = self._counts.get(path) or pdf_page_count(path)
                stamp = source_stamp(path)
            except PdfOpError as e:
                notes.append(f"{name}: {e}")
                continue
            except OSError as e:
                notes.append(describe_lock_failure(path, e))
                continue
            except Exception:
                notes.append(f"{name} could not be read")
                continue
            take = min(n, room)
            if n > take:
                notes.append(f"only the first {take} of {plural(n, 'page')} in {name} were taken "
                             f"(an insert takes up to {CLIP_MAX_PAGES})")
            entries += [{"path": path, "sidx": i, "stamp": stamp, "rot": 0, "img": None} for i in range(take)]
        if not entries:
            self._set_status("Nothing was inserted" + (f": {'; '.join(notes)}." if notes else "."), "error")
            return
        before_n = len(self._pages)
        self.paste_pages(before=before, entries=entries, at=at)
        if len(self._pages) > before_n and notes:
            self._set_status(self.status.text() + " Note: " + "; ".join(notes) + ".", "error")

    def _on_files_dropped(self, paths, target):
        self.insert_pdf_files(paths, at=target)

    def insert_from_files(self, before=False):
        """Menu: pick PDF files and put all their pages in."""
        if self._busy or not self._pages:
            return
        start = self.folder_row.path()
        files, _ = QFileDialog.getOpenFileNames(self, "Choose the PDFs to take pages from",
                                                start if os.path.isdir(start) else "", "PDF Files (*.pdf)")
        if files:
            self.insert_pdf_files(files, before=before)

    @staticmethod
    def _can_paste() -> bool:
        return bool(len(_PAGE_CLIP)) or bool(clipboard_pdf_paths())

    # ------------------------------------------------------------ B pane (a read-only source beside A)
    def toggle_compare(self):
        if self.b_pane.isVisible():
            self.close_b()
            return
        self.b_pane.setVisible(True)
        total = max(2, self.pages_split.width())
        self.pages_split.setSizes([total // 2, total // 2])
        self.compare_btn.setText("Hide B")
        if not self._b_path:
            self.choose_b()

    def close_b(self):
        self._stop_b()
        self._b_path, self._b_stamp, self._b_count, self._b_thumbs = "", None, 0, {}
        self._b_epoch += 1
        self._b_serial += 1
        self.b_pane.grid.clear()
        self.b_pane.grid.serial = self._b_serial
        self.b_pane.title.setText("B: no PDF chosen")
        self.b_pane.set_enabled_state(False)
        self.b_pane.setVisible(False)
        self.compare_btn.setText("Compare\u2026")

    def _stop_b(self):
        for w in self._bg:
            if isinstance(w, _BWorker):
                w.cancel()

    def _build_b_menu(self):
        """The 'Choose PDF' menu: the PDFs in this folder, then Browse."""
        menu = QMenu(self)
        shown = 0
        for r in range(self.file_list.count()):
            path = self._row_path(self.file_list.item(r))
            if not path:
                continue
            if shown >= 40:
                break
            shown += 1
            act = menu.addAction(os.path.basename(path), _later(self.open_b, path))
            act.setCheckable(True)
            act.setChecked(os.path.abspath(path) == self._b_path)
        if shown:
            menu.addSeparator()
        menu.addAction("Browse for another PDF\u2026", _later(self._browse_b))
        return menu

    def choose_b(self):
        menu = self._build_b_menu()
        btn = self.b_pane.buttons["choose"]
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        menu.deleteLater()

    def _on_b_files_dropped(self, paths):
        """Files dropped on the B pane: the first PDF becomes B. B is only ever read."""
        files = [p for p in paths if isinstance(p, str) and os.path.isfile(p)]
        pdfs = classify_drop(files)["pdfs"]
        if not pdfs:
            self._set_status("Drop a PDF file on B to compare it (a folder or another kind of file cannot be shown there).",
                             "error")
            return
        if self.open_b(pdfs[0]) and len(pdfs) > 1:
            self._set_status(f"Showing {os.path.basename(pdfs[0])} in B. B holds one PDF at a time; the other "
                             f"{plural(len(pdfs) - 1, 'PDF')} {'was' if len(pdfs) == 2 else 'were'} not used.", "")

    def _browse_b(self):
        start = self.folder_row.path()
        path, _ = QFileDialog.getOpenFileName(self, "Choose the PDF to take pages from",
                                              start if os.path.isdir(start) else "", "PDF Files (*.pdf)")
        if path:
            self.open_b(path)

    def open_b(self, path: str) -> bool:
        """Show `path` in the B pane. It is only ever read."""
        path = os.path.abspath(path or "")
        name = os.path.basename(path)
        if not os.path.isfile(path):
            self._set_status(f"{name or 'That file'} is not there any more.", "error")
            return False
        problem = pdf_file_problem(path)
        if problem:
            self._set_status(problem, "error")
            return False
        try:
            stamp = source_stamp(path)
        except OSError as e:
            self._set_status(describe_lock_failure(path, e), "error")
            return False
        self._stop_b()
        self._b_epoch += 1
        self._b_serial += 1
        self._b_path, self._b_stamp, self._b_count, self._b_thumbs = path, stamp, 0, {}
        self.b_pane.grid.clear()
        self.b_pane.grid.serial = self._b_serial
        self.b_pane.title.setText(f"B: {name} - opening\u2026")
        self.b_pane.set_enabled_state(False)
        if not self.b_pane.isVisible():
            self.b_pane.setVisible(True)
            self.compare_btn.setText("Hide B")
        if self._shutting_down:
            return False
        w = self._track(_BWorker(path, self._thumb_width() * 2, self._b_epoch))
        w.total.connect(self._on_b_total)
        w.image.connect(self._on_b_image)
        w.failed.connect(self._on_b_failed)
        w.start()
        return True

    def _b_icon(self, img):
        cell = self._cell()
        canvas = QPixmap(cell)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if img is None:
            painter.fillRect(canvas.rect().adjusted(2, 2, -2, -2), QColor(theme_color("row_alt")))
        else:
            pm = QPixmap.fromImage(img).scaled(cell, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            px, py = (cell.width() - pm.width()) // 2, (cell.height() - pm.height()) // 2
            painter.drawPixmap(px, py, pm)
            edge = QPen(QColor(theme_color("border")))
            edge.setWidth(1)
            painter.setPen(edge)
            painter.drawRect(px, py, pm.width() - 1, pm.height() - 1)
        painter.end()
        return QIcon(canvas)

    def _on_b_total(self, epoch, n):
        if epoch != self._b_epoch or not self._b_path:
            return
        self._b_count = n
        g = self.b_pane.grid
        g.blockSignals(True)
        g.clear()
        g.setIconSize(self._cell())
        for i in range(min(n, B_PAGES_MAX)):
            it = QListWidgetItem(self._b_icon(None), f"Page {i + 1}\n ")
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            g.addItem(it)
        g.blockSignals(False)
        cap = f" (showing the first {B_PAGES_MAX})" if n > B_PAGES_MAX else ""
        self.b_pane.title.setText(f"B: {os.path.basename(self._b_path)} - {plural(n, 'page')}{cap}")
        self.b_pane.set_enabled_state(True)

    def _on_b_image(self, epoch, sidx, img):
        if epoch != self._b_epoch:
            return
        self._b_thumbs[sidx] = img
        it = self.b_pane.grid.item(sidx)
        if it is not None:
            it.setIcon(self._b_icon(img))

    def _on_b_failed(self, epoch, message):
        if epoch != self._b_epoch:
            return
        self._b_path, self._b_stamp, self._b_count, self._b_thumbs = "", None, 0, {}
        self.b_pane.grid.clear()
        self.b_pane.title.setText("B: could not open that PDF")
        self.b_pane.set_enabled_state(False)
        self._set_status(message, "error")

    def _b_selected_rows(self):
        return sorted(self.b_pane.grid.row(i) for i in self.b_pane.grid.selectedItems())

    def _b_entries(self, rows):
        """B pages as the same entries the clipboard holds."""
        if not self._b_path:
            return []
        cell = self._cell()
        out = []
        for r in rows:
            if not (0 <= r < min(self._b_count, B_PAGES_MAX)):
                continue
            img = self._b_thumbs.get(r)
            if img is not None:
                img = img.scaled(cell, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            out.append({"path": self._b_path, "sidx": r, "stamp": self._b_stamp, "rot": 0, "img": img})
        return out

    def _b_need_selection(self):
        rows = self._b_selected_rows()
        if not rows:
            self._set_status("Click one or more pages in B first (Ctrl or Shift to pick several).", "error")
        return rows

    def b_copy(self):
        rows = self._b_need_selection()
        if not rows:
            return
        self._cancel_cut()
        _PAGE_CLIP.set(self._b_entries(rows))
        n = len(_PAGE_CLIP)
        self._set_status(f"Copied {plural(n, 'page')} from B. Click where they should go in A and press Ctrl+V.", "")
        self._update_info()

    def b_insert(self, before=False):
        """Insert the selected B pages into A after A's selected page (before it with `before`), else at A's end."""
        rows = self._b_need_selection()
        if rows:
            self.paste_pages(before=before, entries=self._b_entries(rows))

    def _on_b_double_clicked(self, _item):
        self.b_insert(False)

    def _on_b_dropped(self, rows, target, serial):
        if serial != self._b_serial or not self._b_path:
            self._set_status("That drag came from a B that is no longer shown.", "error")
            return
        self.paste_pages(entries=self._b_entries(rows), at=target)

    def _build_b_page_menu(self, pos):
        g = self.b_pane.grid
        item = g.itemAt(pos)
        if item is None or not self._b_path:
            return None
        if not item.isSelected():
            g.clearSelection()
            item.setSelected(True)
        n = len(g.selectedItems())
        what = "this page" if n == 1 else f"these {n} pages"
        menu = QMenu(self)
        menu.addAction(f"Copy {what}\tCtrl+C", _later(self.b_copy))
        menu.addSeparator()
        menu.addAction(f"Insert {what} after the selected page in A", _later(self.b_insert, False))
        menu.addAction(f"Insert {what} before the selected page in A", _later(self.b_insert, True))
        return menu

    def _b_menu(self, pos):
        menu = self._build_b_page_menu(pos)
        if menu is not None:
            menu.exec(self.b_pane.grid.viewport().mapToGlobal(pos))
            menu.deleteLater()

    def swap_ab(self):
        """Make B the PDF being built and show the current A as the source. The PDF becoming A must be in
        this folder's list, and unsaved changes to the old A are dropped (asked first)."""
        if self._busy:
            return
        a_path, b_path = self._cur_path, self._b_path
        if not (a_path and b_path):
            self._set_status("Swap needs a PDF open in both A and B.", "error")
            return
        row = next((r for r in range(self.file_list.count())
                    if os.path.abspath(self._row_path(self.file_list.item(r)) or "") == b_path), -1)
        if row < 0:
            self._set_status("B is from another folder, so it cannot become A here. Open its folder first.", "error")
            return
        if self._is_dirty() and Msg.question(
                self, "Swap A and B",
                f"{os.path.basename(a_path)} has changes you have not saved. Swapping drops them.\n\n"
                "Swap anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._loading_row = True
        self.file_list.setCurrentRow(row)
        self._loading_row = False
        self._open_file(b_path)
        self.open_b(a_path)
        self._set_status(f"A is now {os.path.basename(b_path)}; B shows {os.path.basename(a_path)}.", "")

    def _on_page_image(self, epoch, tok, img):
        if epoch != self._clip_epoch or tok not in self._all:
            return
        self._thumbs[tok] = img
        self._refresh_items(only_idx=tok)

    def _copy_file_pages(self, path):
        """File list -> Copy all its pages: put every page of that PDF on the clipboard without opening it."""
        if self._busy:
            return
        try:
            require_safe_to_expand(path)
            n = self._counts.get(path) or pdf_page_count(path)
            stamp = source_stamp(path)
        except PdfOpError as e:
            self._set_status(str(e), "error")
            return
        except OSError as e:
            self._set_status(describe_lock_failure(path, e), "error")
            return
        take = min(n, CLIP_MAX_PAGES)
        self._cancel_cut()
        _PAGE_CLIP.set([{"path": path, "sidx": i, "stamp": stamp, "rot": 0, "img": None} for i in range(take)])
        cut = f" (the clipboard keeps {CLIP_MAX_PAGES}; the first {CLIP_MAX_PAGES} were taken)" if n > take else ""
        self._set_status(f"Copied all {plural(take, 'page')} of {os.path.basename(path)}{cut}. "
                         "Open another PDF, click where they should go and press Ctrl+V.", "")
        self._update_info()

    def _drop_pages_by_idx(self, idxs) -> int:
        """Remove the pages whose original index is in `idxs` (one Undo step).
        Never empties the PDF. Returns how many pages left the grid."""
        gone = {r for r, p in enumerate(self._pages) if p["idx"] in idxs}
        if not gone or self._would_empty(gone):
            return 0
        self._push_undo()
        self._pages = [p for r, p in enumerate(self._pages) if r not in gone]
        self._rebuild_grid()
        return len(gone)

    def move_earlier(self):
        self._move(-1)

    def move_later(self):
        self._move(1)

    def _move(self, step):
        pos = self._need_selection()
        if not pos:
            return
        keep = self._selected_idx()
        pages = self._pages
        # `flags` travels with the pages as they swap, so a block of adjacent
        # selected pages moves as a block (a fixed set of positions would let
        # only the block's edge page move).
        flags = [r in set(pos) for r in range(len(pages))]
        order = list(range(len(pages)))
        if step < 0:
            for r in range(1, len(pages)):
                if flags[r] and not flags[r - 1]:
                    order[r - 1], order[r] = order[r], order[r - 1]
                    flags[r - 1], flags[r] = flags[r], flags[r - 1]
        else:
            for r in range(len(pages) - 2, -1, -1):
                if flags[r] and not flags[r + 1]:
                    order[r + 1], order[r] = order[r], order[r + 1]
                    flags[r + 1], flags[r] = flags[r], flags[r + 1]
        if order != list(range(len(pages))):
            self._push_undo()
        self._pages = [pages[i] for i in order]
        self._rebuild_grid(keep)
        first = min(self.grid.row(i) for i in self.grid.selectedItems()) if self.grid.selectedItems() else 0
        self.grid.scrollToItem(self.grid.item(first))

    def select_blank(self):
        self.grid.clearSelection()
        n = 0
        for pos, page in enumerate(self._pages):
            if page["blank"]:
                self.grid.item(pos).setSelected(True)
                n += 1
        self._set_status(
            f"{plural(n, 'page')} look blank and are selected. Press Delete page to drop them."
            if n else "No pages look blank.", "" if n else "error")

    def reset_pages(self):
        keep = self._selected_idx()
        if self._is_dirty():
            self._push_undo()
        for p in self._all.values():
            p["rot"] = 0
            p.pop("cut", None)
        _PAGE_CLIP.cut = None
        self._pages = [self._all[i] for i in sorted(self._all) if i < PASTE_BASE]       # pasted pages go; Undo brings them back
        self._rebuild_grid(keep)
        self._set_status("This PDF is back to how it was opened.", "")

    def _update_info(self):
        if not self._pages:
            return
        bits = [f"{plural(self._total, 'page')} in the file"]
        if self._is_dirty():
            bits.append(f"{plural(len(self._live_pages()), 'page')} will be saved")
        n_sel = len(self.grid.selectedItems())
        if n_sel:
            bits.append(f"{n_sel} selected")
        if len(_PAGE_CLIP):
            bits.append(f"{plural(len(_PAGE_CLIP), 'page')} on the clipboard (Ctrl+V pastes)")
        self.info_label.setText(" · ".join(bits) + ".")

    # ------------------------------------------------------------ saving
    def save_and_next(self):
        self._start_save(advance_to="__next__")

    def save_if_dirty(self):
        """Ctrl+S: write the changes, or do nothing. (Save & Next on a clean PDF moves on; a habitual Ctrl+S must not skip files.)"""
        if self._cur_path and self._is_dirty():
            self.save_and_next()
        elif self._cur_path:
            self._set_status("Nothing to save: this PDF has no changes.", "")

    def skip_next(self):
        if self._busy:
            return
        if self._is_dirty():
            ans = self._ask_save(os.path.basename(self._cur_path))
            if ans == QMessageBox.Cancel:
                return
            if ans == QMessageBox.Save:
                self._start_save(advance_to="__next__")
                return
        if self._cur_path:
            self._file_state.setdefault(self._cur_path, "skipped")
            for i in range(self.file_list.count()):
                if self._row_path(self.file_list.item(i)) == self._cur_path:
                    self._refresh_row(self.file_list.item(i))
        self._advance("__next__")

    def _start_save(self, advance_to):
        if self._busy:
            return
        if not self._cur_path:
            self._set_status("No PDF is open. Choose a folder, then click a PDF in the list.", "error")
            return
        plan = self._plan()
        if not self._is_dirty():
            self._set_status("No changes to save here — moving on.", "")
            self._advance(advance_to)
            return
        if not plan:
            self._set_status("A PDF needs at least one page. Leave at least one page in.", "error")
            return
        keep_sig = False
        if self._has_texts():
            choice = self._signed_text_choice(plan)
            if choice == "cancel":
                self._set_status("Not saved. The text is still on the pages.", "")
                return
            keep_sig = choice == "keep"
        self._release_source_for_replace()
        self._begin_op(self._cur_path)
        self._advance_after = advance_to
        self._set_status(f"Saving {os.path.basename(self._cur_path)}…", "busy")
        extra, extra_stamps = self._extra_sources()
        self._worker = _SaveWorker("organize", self._cur_path, self._cur_stamp, plan,
                                   bool(setting("organizer_replace_original")),
                                   extra=extra or None, extra_stamps=extra_stamps or None,
                                   texts=self._texts_for_plan(), keep_signatures=keep_sig,
                                   strip_own=not setting("organizer_keep_scripts"))
        self._worker.done.connect(self._on_saved)
        self._worker.failed.connect(self._on_save_failed)
        self._worker.finished.connect(self._save_finished)
        self._worker.start()

    def _signed_text_choice(self, plan) -> str:
        """'none' (file is not signed), 'keep', 'remove' or 'cancel'. Text in a signed file is only added when the
        person says so, for an approved item."""
        try:
            multi = is_multi_plan(plan)
            opts = signed_text_options(self._cur_path, plan, self._total, multi, self._texts_for_plan())
        except Exception:
            return "none"
        if not opts["signed"]:
            return "none"
        return _ask_signed_choice(self, opts)

    def _release_source_for_replace(self):
        """Replacing the original needs the thumbnail renderer to have let go
        of it (Windows refuses to replace a file another handle has open)."""
        if setting("organizer_replace_original"):
            for w in self._bg:
                if isinstance(w, _ThumbWorker):
                    w.cancel()
                    w.wait(8000)

    def _save_finished(self):
        self._end_op()

    def _on_saved(self, res):
        self._end_op()
        if self._shutting_down:
            return
        path = self._op_path
        if res.get("kind") == "extract":
            pending, self._extract_pending = self._extract_pending, None
            removed = 0
            if pending and self._cur_path == path:
                removed = self._drop_pages_by_idx(pending)
            self._set_tools_enabled(bool(self._pages) and self._cur_path == path)
            msg = f"Extracted {plural(res['pages'], 'page')} to {res['path']}"
            if self._note_output_file(res["path"], res["pages"], "new"):
                msg += ". It is in the list now"
            if removed:
                msg += (f". Deleted {'it' if removed == 1 else 'them'} from this PDF "
                        "(Undo brings back the page" + ("" if removed == 1 else "s") + ")")
            elif pending:
                msg += ". The pages were not deleted from this PDF."
            self._set_status(msg, "ok")
            return
        self._file_state[path] = "saved"
        where = "replaced the original (a copy is in its Originals folder)" \
            if res.get("backup") else f"saved to {os.path.dirname(res['path'])}"
        if res.get("edited"):
            where = f"saved as {os.path.basename(res['path'])} in {os.path.dirname(res['path'])}; the original is untouched"
        if res.get("backup"):
            self._counts[path] = res["pages"]
        self._note_output_file(res["path"], res["pages"], None)
        note = ""
        if res.get("signature_kept"):
            note += " The digital signature is kept; viewers will show the file was changed after it was signed."
        elif res.get("signature_removed"):
            note += " The digital signature is not in the saved copy."
        if res.get("text_removed") and not res.get("text_covered"):
            note += " The old words you replaced are gone from the page."
        elif res.get("text_covered"):
            note += (f" {plural(res['text_covered'], 'line')} of old text could not be taken off the page, so "
                     "it is hidden under a patch but is still inside the file (copying or searching the page can find it).")
        if res.get("was_encrypted"):
            note += " The original's editing restrictions were not carried over."
        if res.get("active_removed"):
            note += (" Scripts and programs inside the file ("
                     + ", ".join(f"{k.lower()}: {v}" for k, v in sorted(res["active_removed"].items()))
                     + ") were left out of the saved copy; links and form fields still work.")
        if res.get("dropped_bookmarks"):
            note += " Bookmarks were not kept because the page order changed."
            if not self._notice_shown:
                self._notice_shown = True
                self._notify("information", "Bookmarks Not Kept",
                             "This PDF had bookmarks. Because pages were moved or removed, the "
                             "bookmarks would point at the wrong pages, so the saved copy has none. "
                             "The original is unchanged.")
        self._set_status(f"{os.path.basename(path)}: {plural(res['pages'], 'page')} {where}.{note}", "ok")
        # The next PDF's "Opening..." line would hide where this one went, so it carries the news.
        self._carry_note = f"Saved {os.path.basename(path)} to {os.path.basename(os.path.dirname(res['path']))}. "
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            if self._row_path(it) == path:
                self._refresh_row(it)
        # The grid still describes the file as it was opened; it is saved now.
        if self._cur_path == path:
            self._pages = []
            self._undo = []
            self._texts = {}
        self._advance(getattr(self, "_advance_after", "__next__"))

    def _on_save_failed(self, message):
        self._extract_pending = None
        self._end_op()
        if self._shutting_down:
            return
        self._set_tools_enabled(bool(self._pages))
        self._set_status(message, "error")
        self._notify("warning", "Could Not Save", message)

    def _advance(self, target):
        """Open the next file (or a named one)."""
        if isinstance(target, str) and target.startswith(_LAUNCH_TARGET):
            k = self._launch_index(target[len(_LAUNCH_TARGET):])
            if k >= 0:
                self._launch_show(k)
            else:
                self._launch_step(1, prompted=True)
            return
        if target == "__next__" and self._launch_active():
            self._launch_step(1, prompted=True)      # the caller already asked about unsaved changes
            return
        n = self.file_list.count()
        if not n:
            return
        row = self.file_list.currentRow()
        if target != "__next__":
            for i in range(n):
                if self._row_path(self.file_list.item(i)) == target:
                    row = i
                    break
            else:
                return
        else:
            # A PDF this panel just extracted is not part of the batch being worked through.
            visible = [i for i in range(row + 1, n)
                       if not self.file_list.item(i).isHidden()
                       and self._file_state.get(self._row_path(self.file_list.item(i))) != "new"]
            if not visible:
                self._clear_grid()
                self.info_label.setText("That was the last PDF in the list.")
                self._set_status("Done with this list. " + self.status.text(), "ok")
                return
            row = visible[0]
        self._loading_row = True
        self.file_list.setCurrentRow(row)
        self._loading_row = False
        self._open_file(self._row_path(self.file_list.item(row)))

    def extract_selected(self):
        pos = self._need_selection()
        if not pos or self._busy:
            return
        if any(self._pasted(self._pages[r]) for r in pos):
            self._set_status("Extract works on pages of the open PDF. Save first, or Copy these pages "
                             "and paste them into another PDF.", "error")
            return
        indices = [self._pages[r]["idx"] for r in pos]
        self._extract_delete = False
        self._extract_pending = None
        target = self._choose_extract_target(len(indices))
        if target is None:
            self._set_status("Extract cancelled.", "")
            return
        if self._busy:
            return
        update_ui_state(organizer_extract_dir=target[0])
        # Only after the file is written does anything leave the grid.
        self._extract_pending = set(indices) if self._extract_delete else None
        self._begin_op(self._cur_path)
        self._set_status("Extracting…", "busy")
        self._worker = _SaveWorker("extract", self._cur_path, self._cur_stamp,
                                   {"indices": indices, "dest_dir": target[0],
                                    "file_name": target[1]}, False)
        self._worker.done.connect(self._on_saved)
        self._worker.failed.connect(self._on_save_failed)
        self._worker.finished.connect(self._save_finished)
        self._worker.start()

    def _choose_extract_target(self, n_pages):
        """(folder, file name) from the operator, or None. Kept separate so a
        test can answer it without a modal dialog."""
        start = load_ui_state().get("organizer_extract_dir")
        if not (isinstance(start, str) and os.path.isdir(start)):
            base = os.path.dirname(os.path.abspath(self._cur_path))
            start = os.path.join(base, setting("organized_dir_name"))
            if not os.path.isdir(start):
                start = base
        stem = os.path.splitext(os.path.basename(self._cur_path))[0]
        dlg = ExtractDialog(self, start, f"{stem}_Extract", n_pages,
                            can_delete=n_pages < len(self._pages))
        try:
            if dlg.exec() != QDialog.Accepted:
                return None
            self._extract_delete = dlg.delete_after()
            return dlg.result()
        finally:
            dlg.deleteLater()

    # ------------------------------------------------------------ right-click menus
    def _build_grid_menu(self, pos):
        """The menu without exec(), so it can be inspected headlessly."""
        item = self.grid.itemAt(pos)
        if not self._pages or self._busy:
            return None
        if item is None:                           # empty space between / after the pages
            return self._build_empty_menu()
        if not item.isSelected():                  # right-click on a page acts on that page
            self.grid.clearSelection()
            item.setSelected(True)
        n = len(self.grid.selectedItems())
        menu = QMenu(self)
        what = "this page" if n == 1 else f"these {n} pages"
        for text, fn in ((f"Rotate {what} right", self.rotate_right),
                         (f"Rotate {what} left", self.rotate_left),
                         (f"Turn {what} upside down", self.rotate_half_turn)):
            menu.addAction(text, _later(fn))
        menu.addSeparator()
        menu.addAction(f"Delete {what}", _later(self.delete_pages))
        menu.addSeparator()
        menu.addAction(f"Cut {what}\tCtrl+X", _later(self.cut_pages))
        menu.addAction(f"Copy {what}\tCtrl+C", _later(self.copy_pages))
        has_clip = self._can_paste()
        menu.addAction("Paste after this page\tCtrl+V", _later(self.paste_pages, False)).setEnabled(has_clip)
        menu.addAction("Paste before this page\tCtrl+Shift+V", _later(self.paste_pages, True)).setEnabled(has_clip)
        menu.addAction("Insert pages from a PDF file after this page…", _later(self.insert_from_files, False))
        menu.addSeparator()
        menu.addAction("Read from this page full size", _later(self.open_reader, self.grid.row(item)))
        menu.addSeparator()
        menu.addAction("Move to the start", _later(self.move_to_start))
        menu.addAction("Move earlier", _later(self.move_earlier))
        menu.addAction("Move later", _later(self.move_later))
        menu.addAction("Move to the end", _later(self.move_to_end))
        menu.addSeparator()
        menu.addAction("Extract to a new PDF…", _later(self.extract_selected))
        menu.addSeparator()
        menu.addAction("Select all pages", _later(self.grid.selectAll))
        menu.addAction("Select blank pages", _later(self.select_blank))
        menu.addSeparator()
        undo = menu.addAction("Undo the last change", _later(self.undo_pages))
        undo.setEnabled(bool(self._undo))
        menu.addAction("Reset this PDF", _later(self.reset_pages))
        return menu

    def _build_empty_menu(self):
        menu = QMenu(self)
        menu.addAction("Paste at the end\tCtrl+V", _later(self.paste_pages, False)).setEnabled(self._can_paste())
        menu.addAction("Insert pages from a PDF file…", _later(self.insert_from_files, False))
        menu.addSeparator()
        menu.addAction("Select all pages", _later(self.grid.selectAll))
        undo = menu.addAction("Undo the last change", _later(self.undo_pages))
        undo.setEnabled(bool(self._undo))
        return menu

    def _grid_menu(self, pos):
        menu = self._build_grid_menu(pos)
        if menu is not None:
            menu.exec(self.grid.viewport().mapToGlobal(pos))
            menu.deleteLater()

    def _build_file_menu(self, pos):
        item = self.file_list.itemAt(pos)
        if item is None or self._busy:
            return None
        path = self._row_path(item)
        menu = QMenu(self)
        menu.addAction("Open this PDF here", _later(self._open_from_menu, path))
        menu.addAction("Read this PDF full size", _later(self._read_from_menu, path))
        menu.addAction("Skip it", _later(self._skip_from_menu, path))
        menu.addAction("Copy all its pages (to paste into another PDF)", _later(self._copy_file_pages, path))
        menu.addAction("Compare: show it in B beside this PDF", _later(self.open_b, path))
        menu.addMenu(build_send_menu(self, "organizer", [path]))
        menu.addSeparator()
        menu.addAction("Open the PDF in your PDF viewer", _later(self._open_external, path))
        menu.addAction("Show it in its folder", _later(self._show_in_folder, path))
        menu.addAction("Copy path", _later(self._copy_path, path))
        return menu

    def _file_menu(self, pos):
        menu = self._build_file_menu(pos)
        if menu is not None:
            menu.exec(self.file_list.viewport().mapToGlobal(pos))
            menu.deleteLater()

    # ------------------------------------------------------------ reader (full-size view)
    def _dock_sync(self, _i=None):
        """In the full-size reader the folder's file list moves in beside the pages; back in the grid it goes home."""
        left = getattr(self, "_files_panel", None)
        if left is None:
            return
        if self.reader is not None and self._reader_active():
            if left.parent() is not self.reader.split:
                self.reader.dock_files(left)
            return
        if self.reader is not None:
            self.reader.undock_files()
        if left.parent() is not self._main_split:
            self._main_split.insertWidget(0, left)
            left.show()
            self._main_split.setStretchFactor(0, 0)
            self._main_split.setStretchFactor(1, 1)
            self._main_split.setSizes([300, 900])

    def _reader_active(self) -> bool:
        return self.reader is not None and self.stack.currentWidget() is self.reader

    def _ensure_reader(self):
        if self.reader is None:
            r = PdfReaderView()
            r.backRequested.connect(self.close_reader)
            r.rotateRequested.connect(self._reader_rotate)
            r.deleteRequested.connect(self._reader_delete)
            r.undoRequested.connect(self.undo_pages)
            r.extractRequested.connect(self._reader_extract)
            r.saveRequested.connect(self.save_and_next)
            r.skipRequested.connect(self.skip_next)
            r.queuePrev.connect(self._launch_prev)
            r.queueNext.connect(self._launch_next)
            r.reloadRequested.connect(self._reader_reload)
            r.contextRequested.connect(self._reader_menu)
            r.textsChanged.connect(self._texts_changed)
            self.reader = r
            self.stack.addWidget(r)
            self._refresh_launch_ui()
        return self.reader

    def _reader_icon(self, pos):
        return self._page_icon(self._pages[pos], strip=True) if 0 <= pos < len(self._pages) else QIcon()

    def _reader_plan(self):
        return [(p["idx"], p["rot"]) for p in self._pages]

    def open_reader(self, at=0):
        """Show the open PDF's pages full size, like a PDF reader. It shows the
        pages as they will be SAVED (order, rotation, removals), so everything
        done in here is the same change as in the grid."""
        if not self._pages or not self._cur_path:
            return False
        self._commit_cut()                                     # the reader shows saved pages only
        at = max(0, min(int(at), len(self._pages) - 1))
        if self._has_pasted():
            return self._open_preview(at)
        self._drop_preview()
        r = self._ensure_reader()
        sel = sorted(self._selected_positions())
        r.open_document(self._cur_path, self._reader_plan(), _weak(self._reader_icon),
                        self._search_text, start=max(0, min(int(at), len(self._pages) - 1)))
        r.set_texts(self._texts)
        r.select_positions(sel, notify=False)
        self.stack.setCurrentWidget(r)
        r.set_status(self.status.text(), self.status.property("kind") or "")
        self._reader_sync_state()
        if self._file_state.get(self._cur_path) in ("changed", "gone"):
            self._reader_note_stale()
        r.canvas.setFocus()
        return True

    def _open_preview(self, at=0):
        """With pasted pages in, the reader shows a temporary copy of the pages as they would be saved.
        It is read-only: change pages in the grid, then open the reader again."""
        if self._shutting_down:
            return False
        self._preview_epoch += 1
        plan = self._plan()
        paths, _stamps = self._extra_sources()
        w = self._track(_PreviewWorker(plan, [self._cur_path] + paths, self._preview_epoch))
        self._preview_at = int(at)
        w.done.connect(self._on_preview_ready)
        w.failed.connect(self._on_preview_failed)
        self._set_status("Preparing a full-size view of the pages as they will be saved\u2026", "busy")
        w.start()
        return True

    def _on_preview_ready(self, epoch, tmp):
        at = self._preview_at
        if epoch != self._preview_epoch or self._shutting_down or not self._has_pasted():
            try:
                os.remove(tmp)
            except OSError:
                pass
            return
        self._drop_preview()
        self._preview_path = tmp
        r = self._ensure_reader()
        sel = sorted(self._selected_positions())
        r.open_document(tmp, [(k, 0) for k in range(len(self._pages))], _weak(self._reader_icon),
                        self._search_text, start=max(0, min(int(at), len(self._pages) - 1)))
        r.select_positions(sel, notify=False)
        self.stack.setCurrentWidget(r)
        r.set_status("Read-only view: it includes pages pasted from other PDFs. Go back to the grid to change pages.", "")
        self._reader_sync_state()
        r.canvas.setFocus()

    def _on_preview_failed(self, epoch, message):
        if epoch == self._preview_epoch:
            self._set_status(message, "error")

    def _drop_preview(self):
        tmp, self._preview_path = self._preview_path, ""
        if tmp:
            try:
                if self.reader is not None and self._reader_active():
                    self.reader.close_document("")
                    self.stack.setCurrentIndex(0)
                elif self.reader is not None:
                    self.reader.close_document("")
            except Exception:
                pass
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _preview_blocks_edit(self) -> bool:
        if self._preview_path and self._reader_active():
            self._set_status("This view includes pasted pages and is read-only. Go back to the grid to change pages.", "error")
            return True
        return False

    def _maybe_open_reader(self):
        want = self._reader_want
        if not want:
            return
        path, t0, at = want
        if path != self._cur_path or time.monotonic() - t0 > 60:
            self._reader_want = None
            if self._reader_active() and path != self._cur_path:
                self._leave_reader_view()
            return
        if not self._pages:
            return
        self._reader_want = None
        self.open_reader(at)

    def _leave_reader_view(self):
        if self.reader is not None and self._reader_active():
            self.reader.close_document("")
            self.stack.setCurrentIndex(0)

    def close_reader(self):
        """Back to the page grid. What was selected or on screen in the reader
        is selected in the grid."""
        self._reader_want = None
        if not self._reader_active():
            return
        r = self.reader
        had_preview = bool(self._preview_path)
        pos = [p for p in (r.selection() or [r.current_page()]) if 0 <= p < len(self._pages)]
        cur = r.current_page()
        r.close_document("")
        self.stack.setCurrentIndex(0)
        self.grid.blockSignals(True)
        self.grid.clearSelection()
        for p in pos:
            it = self.grid.item(p)
            if it is not None:
                it.setSelected(True)
        self.grid.blockSignals(False)
        it = self.grid.item(min(cur, self.grid.count() - 1)) if self.grid.count() else None
        if it is not None:
            self.grid.scrollToItem(it)
        self._update_info()
        self.grid.setFocus()
        if had_preview:
            self._drop_preview()

    def _reader_sync_state(self):
        if self.reader is not None:
            self.reader.set_enabled_state(bool(self._pages), self._busy,
                                          bool(self._undo) and bool(self._pages) and not self._busy)
            self.reader.set_dirty(bool(self._undo) and bool(self._pages))
            if self._preview_path:                    # the read-only view of pasted pages cannot take text
                self.reader.text_btn.setEnabled(False)

    def _reader_pages_changed(self):
        """The page list changed (rotate, delete, move, undo, reset): the reader
        redraws from the same list."""
        if self._preview_path:
            self._preview_epoch += 1               # the picture no longer matches the pages: back to the grid
            if self._reader_active():
                self.close_reader()
            self._drop_preview()
            return
        if self.reader is not None and self._reader_active():
            self.reader.set_pages(self._reader_plan(), _weak(self._reader_icon))
            self._reader_sync_state()

    def _reader_note_stale(self):
        if self._reader_active():
            gone = self._file_state.get(self._cur_path) == "gone"
            self.reader.show_stale(
                f"{os.path.basename(self._cur_path)} is no longer in the folder. You can still read it here."
                if gone else
                f"{os.path.basename(self._cur_path)} changed on disk after you opened it. "
                "What you see is the version you opened; saving will be refused.")

    def _reader_targets(self):
        pos = self.reader.selection() or [self.reader.current_page()]
        return [p for p in pos if 0 <= p < len(self._pages)]

    def _reader_apply_selection(self):
        """Make the grid's selection the pages the reader means, so the grid's
        own operations (which read it) act on exactly those pages."""
        pos = self._reader_targets()
        self.grid.blockSignals(True)
        self.grid.clearSelection()
        for p in pos:
            it = self.grid.item(p)
            if it is not None:
                it.setSelected(True)
        self.grid.blockSignals(False)
        return pos

    def _reader_rotate(self, delta):
        if self._preview_blocks_edit():
            return
        if self._busy or not self._reader_active() or not self._reader_apply_selection():
            return
        self._rotate(delta)

    def _reader_delete(self):
        if self._preview_blocks_edit():
            return
        if self._busy or not self._reader_active() or not self._reader_apply_selection():
            return
        self.delete_pages()

    def _reader_extract(self):
        if self._preview_blocks_edit():
            return
        if self._busy or not self._reader_active() or not self._reader_apply_selection():
            return
        self.extract_selected()

    def _reader_reload(self):
        path = self._cur_path
        if not path or self._busy:
            return
        if self._is_dirty():
            ans = Msg.question(self, "Reload This PDF",
                               "Reloading reads the file again and throws away the page changes "
                               "you have made to it so far.\n\nReload it?",
                               QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        self._file_state.pop(path, None)
        self._open_file(path)

    def _reader_menu(self, global_pos):
        menu = self._build_reader_menu()
        if menu is not None:
            menu.exec(global_pos)
            menu.deleteLater()

    def _build_reader_menu(self):
        """The reader's right-click menu without exec(), so it can be inspected headlessly."""
        if not self._reader_active() or self._busy:
            return None
        pos = self._reader_targets()
        if not pos:
            return None
        what = "this page" if len(pos) == 1 else f"these {len(pos)} pages"
        menu = QMenu(self)
        if self.reader.add_text_menu(menu):
            menu.addSeparator()
        menu.addAction(f"Rotate {what} right", _later(self._reader_rotate, 90))
        menu.addAction(f"Rotate {what} left", _later(self._reader_rotate, -90))
        menu.addAction(f"Turn {what} upside down", _later(self._reader_rotate, 180))
        menu.addSeparator()
        menu.addAction(f"Delete {what}", _later(self._reader_delete))
        menu.addAction(f"Extract {what}…", _later(self._reader_extract))
        undo = menu.addAction("Undo the last change", _later(self.undo_pages))
        undo.setEnabled(bool(self._undo))
        menu.addSeparator()
        menu.addAction("Back to the page grid", _later(self.close_reader))
        return menu

    def _escape_pressed(self) -> bool:
        """Esc leaves the reader (after clearing its find box). Anywhere else it
        keeps meaning 'stop the running search' (MainWindow._escape)."""
        if not self._reader_active():
            return False
        if not self.reader.handle_escape():
            self.close_reader()
        return True

    def _on_file_double_clicked(self, item):
        path = self._row_path(item)
        if not path or path != self._cur_path:     # a refused switch (unsaved changes) leaves the old PDF open
            return
        self._reader_want = (path, time.monotonic(), 0)
        self._maybe_open_reader()

    def _on_page_double_clicked(self, item):
        self.open_reader(self.grid.row(item))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and not event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier):
            if obj is self.grid and self._pages:
                sel = self._selected_positions()
                self.open_reader(sel[0] if sel else 0)
                return True
            if obj is self.file_list:
                item = self.file_list.currentItem()
                if item is not None and self._row_path(item) == self._cur_path:
                    self._on_file_double_clicked(item)
                    return True
        return super().eventFilter(obj, event)

    def _read_from_menu(self, path):
        self._open_from_menu(path)
        if self._cur_path == path:
            self._on_file_double_clicked(self.file_list.currentItem())

    def _open_from_menu(self, path):
        for i in range(self.file_list.count()):
            if self._row_path(self.file_list.item(i)) == path:
                self.file_list.setCurrentRow(i)
                return

    # ------------------------------------------------------------ PDFs launched from Explorer
    @staticmethod
    def _qkey(path):
        return os.path.normcase(os.path.abspath(path))

    def _launch_index(self, path):
        k = self._qkey(path)
        for i, p in enumerate(self._launch_q):
            if self._qkey(p) == k:
                return i
        return -1

    def launch_waiting(self) -> int:
        return sum(1 for p in self._launch_q if self._launch_state.get(p) == "waiting")

    def receive_folder(self, folder: str) -> bool:
        """From the tray: list this folder's PDFs."""
        if not os.path.isdir(folder):
            self._set_status("That folder is not there any more.", "error")
            return False
        self.folder_row.set_path(folder)
        return True

    def receive_paths(self, paths):
        """Another tab sent PDFs: they join the list like a double-click from Explorer."""
        pdfs = classify_drop(paths)["pdfs"]
        if not pdfs:
            self._set_status("None of those are PDFs the Page Organizer can open.", "error")
            return False
        return all([self.open_pdf_in_reader(p) for p in pdfs])

    def open_pdf_in_reader(self, path: str) -> bool:
        """A PDF handed over by Windows (double-click / Open with) or a later launch.
        It always joins the queue; it is shown at once only when nothing else is
        waiting and it is not part of a burst of launches. False, with a status
        line, when the file is unusable or the queue is full."""
        path = os.path.abspath(path or "")
        if not (os.path.isfile(path) and path.lower().endswith(".pdf")):
            self._set_status("That file is not a PDF I can open.", "error")
            return False
        now = time.monotonic()
        burst = (now - self._launch_t) < LAUNCH_BURST_S
        self._launch_t = now
        i = self._launch_index(path)
        added = i < 0
        if added:
            if len(self._launch_q) >= LAUNCH_QUEUE_MAX:
                self._set_status(f"The launch list is full ({LAUNCH_QUEUE_MAX} PDFs). Use Clear viewed, "
                                 "then open this PDF again.", "error")
                return False
            self._launch_q.append(path)
            self._launch_state[path] = "waiting"
            i = len(self._launch_q) - 1
        self._refresh_launch_ui()
        others = sum(1 for j, p in enumerate(self._launch_q)
                     if j != i and self._launch_state.get(p) == "waiting")
        if burst or self._launch_guard or self._busy or (added and others):
            w = self.launch_waiting()
            self._set_status(f"{os.path.basename(path)} added to the list — {w} waiting." if added
                             else f"{os.path.basename(path)} is already in the list.", "")
            return True
        return self._launch_goto(i)

    def _launch_goto(self, i, prompted=False) -> bool:
        if not (0 <= i < len(self._launch_q)) or self._busy or self._launch_guard:
            return False
        path = self._launch_q[i]
        if not os.path.isfile(path):
            self._launch_state[path] = "missing"
            self._refresh_launch_ui()
            self._set_status(f"{os.path.basename(path)} is no longer there, so it was skipped.", "error")
            return False
        if self._is_dirty() and self._qkey(path) != self._qkey(self._cur_path or ""):
            if not prompted:
                ans = self._ask_save(os.path.basename(self._cur_path))
                if ans == QMessageBox.Cancel:
                    self._set_status(f"Staying on {os.path.basename(self._cur_path)} — it has unsaved changes. "
                                     f"{self.launch_waiting()} PDF(s) still waiting.", "")
                    return False
                if ans == QMessageBox.Save:
                    self._start_save(advance_to=_LAUNCH_TARGET + path)
                    return True
            self._open_file("")                      # asked and Discarded (here or by the caller): drop the old pages, no second prompt
        return self._launch_show(i)

    def _launch_show(self, i) -> bool:
        path = self._launch_q[i]
        folder = os.path.dirname(path)
        if os.path.normcase(os.path.abspath(folder)) != os.path.normcase(os.path.abspath(self._folder_loaded or "?")):
            self.folder_row.set_path(folder)
        self._read_from_menu(path)
        if self._qkey(self._cur_path or "") != self._qkey(path):      # it did not open: stay "waiting", say so
            self._set_status(f"{os.path.basename(path)} could not be opened from the list.", "error")
            return False
        self._launch_pos = i
        self._launch_state[path] = "viewed"
        self._refresh_launch_ui()
        return True

    def _launch_step(self, step, prompted=False) -> bool:
        if self._busy or self._launch_guard or len(self._launch_q) < LAUNCH_UI_MIN:
            return False
        self._launch_dir = 1 if step > 0 else -1
        j = (self._launch_pos if self._launch_pos >= 0 else (-1 if step > 0 else len(self._launch_q))) + step
        skipped = 0
        while 0 <= j < len(self._launch_q):
            p = self._launch_q[j]
            if self._launch_state.get(p) == "failed":      # already reported when it failed
                j += step
                continue
            if self._launch_state.get(p) == "missing" or not os.path.isfile(p):
                self._launch_state[p] = "missing"
                skipped += 1
                j += step
                continue
            self._refresh_launch_ui()
            if skipped:                         # shown in front of the next PDF's "N pages" line
                self._carry_note = f"Skipped {plural(skipped, 'PDF')} that {'is' if skipped == 1 else 'are'} no longer there. "
            ok = self._launch_goto(j, prompted)
            if not ok:
                self._carry_note = ""
            return ok
        self._refresh_launch_ui()
        self._set_status("That was the last PDF on the list." if step > 0
                         else "This is the first PDF on the list.", "")
        return False

    def _launch_next(self):
        self._launch_step(1)

    def _launch_prev(self):
        self._launch_step(-1)

    def _launch_active(self) -> bool:
        i = self._launch_pos
        return (len(self._launch_q) >= LAUNCH_UI_MIN and 0 <= i < len(self._launch_q)
                and bool(self._cur_path) and self._qkey(self._launch_q[i]) == self._qkey(self._cur_path))

    def _on_launch_clicked(self, item):
        self._launch_goto(self.launch_list.row(item))

    def launch_clear_viewed(self):
        if self._busy:
            return
        before = len(self._launch_q)
        cur = self._launch_q[self._launch_pos] if 0 <= self._launch_pos < len(self._launch_q) else None
        keep = [p for p in self._launch_q if self._launch_state.get(p) == "waiting" or p == cur]
        self._launch_q = keep
        self._launch_state = {p: self._launch_state[p] for p in keep}
        self._launch_pos = keep.index(cur) if cur in keep else -1
        self._refresh_launch_ui()
        self._set_status(f"Took {plural(before - len(keep), 'PDF')} off the list.", "")

    def _refresh_launch_ui(self):
        q, n = self._launch_q, len(self._launch_q)
        pos, waiting = self._launch_pos, self.launch_waiting()
        show = n >= LAUNCH_UI_MIN
        self.launch_panel.setVisible(show)
        self.launch_list.blockSignals(True)
        self.launch_list.clear()
        for p in q:
            st = self._launch_state.get(p)
            it = QListWidgetItem(("✓ " if st == "viewed" else "! " if st in ("missing", "failed") else "· ")
                                 + os.path.basename(p) + (" (missing)" if st == "missing" else
                                                          " (could not be opened)" if st == "failed" else ""))
            it.setToolTip(p)
            it.setData(Qt.AccessibleTextRole, f"{os.path.basename(p)}, {st or 'waiting'}")
            self.launch_list.addItem(it)
        if 0 <= pos < n:
            self.launch_list.setCurrentRow(pos)
        self.launch_list.blockSignals(False)
        if n:
            rh = max(self.launch_list.sizeHintForRow(0), 20)
            self.launch_list.setMaximumHeight(min(n, 5) * rh + 2 * self.launch_list.frameWidth() + 2)   # whole rows
        self.launch_label.setText(f"Opened from Explorer — {pos + 1 if pos >= 0 else '–'} of {n} · {waiting} waiting. "
                                  "This list is not kept when you close the app.")
        free = not self._busy
        self.launch_prev_btn.setEnabled(free and pos > 0)
        self.launch_next_btn.setEnabled(free and pos < n - 1)
        self.launch_clear_btn.setEnabled(free and any(self._launch_state.get(p) != "waiting" for p in q))
        if self.reader is not None:
            self.reader.set_queue(pos, n, waiting)

    def _skip_from_menu(self, path):
        self._file_state.setdefault(path, "skipped")
        for i in range(self.file_list.count()):
            if self._row_path(self.file_list.item(i)) == path:
                self._refresh_row(self.file_list.item(i))

    def _open_external(self, path):
        open_file(path)

    def _show_in_folder(self, path):
        open_in_explorer(os.path.dirname(path))

    def _copy_path(self, path):
        QApplication.clipboard().setText(path)
        self._set_status("Path copied.", "ok")

    # ------------------------------------------------------------ files dropped from Explorer
    def _dropped_target(self, event):
        """(folder, file to select or "") from a drop of a folder or PDF."""
        if not event.mimeData().hasUrls():
            return None
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local and os.path.isdir(local):
                return local, ""
            if local and os.path.isfile(local) and local.lower().endswith(".pdf"):
                return os.path.dirname(local), local
        return None

    def dragEnterEvent(self, event):
        if not self._busy and self._dropped_target(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        target = self._dropped_target(event)
        if not target or self._busy:
            event.ignore()
            return
        folder, select = target
        event.acceptProposedAction()
        self._pending_select = select
        if os.path.normcase(os.path.abspath(folder)) == os.path.normcase(os.path.abspath(self._folder_loaded or "?")):
            self._select_pending()
        else:
            self.folder_row.set_path(folder)
            self._select_pending_later()

    def _select_pending(self):
        sel, self._pending_select = getattr(self, "_pending_select", ""), ""
        if sel:
            self._open_from_menu(sel)

    def _select_pending_later(self):
        # the folder list fills synchronously in _load_folder, so the file is already there
        self._select_pending()

    def _open_output(self):
        base = self.folder_row.path()
        out = os.path.join(base, setting("organized_dir_name")) if base else ""
        if not (out and os.path.isdir(out) and open_in_explorer(out)):
            self._set_status("Nothing has been saved to the Reorganized folder yet.", "error")

    # ------------------------------------------------------------ search
    def run_search(self):
        needle = self.search_edit.text().strip()
        folder = self.folder_row.path()
        if not needle:
            self.clear_search()
            return
        if not folder or not self.file_list.count():
            self._set_status("Choose a folder with PDFs first.", "error")
            return
        for w in self._bg:
            if isinstance(w, _SearchWorker):
                w.cancel()
        self._search_text = needle
        self._matches = {}
        paths = [self._row_path(self.file_list.item(i)) for i in range(self.file_list.count())]
        self._set_status(f"Searching {plural(len(paths), 'PDF')} for “{needle}”…", "busy")
        self._search_gen += 1
        w = self._track(_SearchWorker(paths, needle, self._search_gen))
        w.result.connect(self._on_search_result)
        w.finished_all.connect(self._on_search_done)
        w.start()

    def _on_search_result(self, token, path, res):
        if token != self._search_gen or not self._search_text:
            return
        self._matches[path] = res
        if path == self._cur_path and self._pages:
            hit = set(res.get("pages", []))
            for p in self._pages:
                p["match"] = p["idx"] in hit
            self._refresh_items()
        # MEASURED 2026-10-12: a linear row search plus a full-list filter per
        # result made 1,500 PDFs cost 11.3 s of UI time (O(n^2)). Rows only
        # change in _load_folder, so the path->row index is exact, and after
        # the first result only the one row that just changed needs filtering.
        row = self._row_of.get(path)
        it = self.file_list.item(row) if row is not None else None
        if it is not None and self._row_path(it) == path:
            self._refresh_row(it)
            it.setForeground(QColor(theme_color("accent")) if res["hits"] else QColor(theme_color("text")))
        if len(self._matches) == 1 or it is None:
            self._apply_filter()
        else:
            self._filter_row(it)

    def _on_search_done(self, token):
        if token != self._search_gen or not self._search_text:
            return
        hit = sum(1 for r in self._matches.values() if r["hits"])
        no_text = sum(1 for r in self._matches.values() if not r["has_text"])
        msg = f"{plural(hit, 'PDF')} contain “{self._search_text}”."
        if no_text:
            msg += f" {plural(no_text, 'PDF')} have no text layer (scans) and cannot be searched."
        self._set_status(msg, "ok" if hit else "")

    def clear_search(self):
        self._search_gen += 1
        for w in self._bg:
            if isinstance(w, _SearchWorker):
                w.cancel()
        self._search_text = ""
        self._matches = {}
        self.search_edit.clear()
        for i in range(self.file_list.count()):
            it = self.file_list.item(i)
            it.setHidden(False)
            it.setForeground(QColor(theme_color("text")))
            self._refresh_row(it)
        for p in self._pages:
            p["match"] = False
        self._refresh_items()
        self._set_status("Search cleared.", "")

    def _filter_row(self, it):
        only = self.only_matches.isChecked() and bool(self._search_text)
        m = self._matches.get(self._row_path(it))
        it.setHidden(bool(only and not (m and m["hits"])))

    def _apply_filter(self, *_a):
        for i in range(self.file_list.count()):
            self._filter_row(self.file_list.item(i))


class LazyTab(QWidget):
    """Holds a panel factory and builds the panel the first time the tab is
    actually shown.

    MEASURED before writing this: constructing all six panels costs ~300ms, of
    which the Bulk Renamer alone is ~157ms - and most launches never open most
    tabs. The window appears sooner and nothing is built that is never looked
    at. The panel is built exactly once and lives for the rest of the session.
    """

    def __init__(self, factory, parent=None):
        super().__init__(parent)
        self._factory = factory
        self.panel = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

    def ensure(self):
        """Build the panel if it does not exist yet. Returns it."""
        if self.panel is None:
            self.panel = self._factory()
            # Each panel sits in a scroll area so the WINDOW is free to be
            # smaller than the panel's own minimum. Measured: the Optimizer
            # alone needs 940px and the Mover 596px, and a QMainWindow cannot
            # go below the largest of them - on a laptop at 125-150% display
            # scaling that is bigger than the screen, so the window could not
            # be shrunk, snapped or fitted at all.
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(self.panel)
            self.layout().addWidget(scroll)
            self._scroll = scroll
            decorate_icons(self.panel)
            apply_tooltips(self.panel)
        return self.panel

    def showEvent(self, event):
        self.ensure()
        super().showEvent(event)


# ---------------------------------------------------------------------
# The Tray layout: a file tray on the left, one button per tool across the top, the same
# tool panels underneath. Nothing inside a panel changes; the tab strip is only hidden.
# Settings > Advanced > Layout switches back to the classic tabs at any time.
# ---------------------------------------------------------------------
TRAY_MAX = 500
TRAY_LARGE_BYTES = 10 * 1024 * 1024
TRAY_VERBS = (
    ("merge", "Merge", "Add one PDF's pages to another", "merge", "PDF Merger"),
    ("split", "Split", "One PDF into many", "scissors", "PDF Splitter"),
    ("shrink", "Optimize", "Smaller, cleaner files", "shrink", "PDF Optimizer"),
    ("pages", "Organize", "Reorder, rotate, compare", "pages", "Page Organizer"),
    ("rename", "Rename", "Many names at once", "tag", "Bulk Renamer"),
    ("move", "Move", "File into folders", "move", "Bulk File Mover"),
)


def expand_folder_pdfs(folder: str, limit: int = TRAY_MAX):
    """The PDFs directly inside a folder, in name order (sub-folders are not searched)."""
    return folder_pdf_listing(folder, limit)[0]


def folder_pdf_listing(folder: str, limit: int = TRAY_MAX):
    """(PDF paths in name order, number of sub-folders not searched, readable)."""
    names, subdirs = [], 0
    try:
        for e in os.scandir(folder):
            if e.is_dir():
                subdirs += 1
            elif e.is_file() and e.name.lower().endswith(".pdf"):
                names.append(e.name)
    except OSError:
        return [], 0, False
    names.sort(key=str.lower)
    return [os.path.join(folder, n) for n in names[:limit]], subdirs, True


def _is_under(path: str, folder: str) -> bool:
    return os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(os.path.abspath(folder))


_FOLDER_BTN = {"rename": "Rename in this folder", "move": "Move from this folder"}


def _folder_label(verb: str, name: str) -> str:
    what = "Rename the files in" if verb == "rename" else "Move files out of"
    return f"{what} the folder {name}. You will see a preview first; nothing changes until you run it."


def plan_tray_feed(verb: str, entries) -> dict:
    """What 'Use them here' would hand to a tool: {"ok", "label", "problem", "paths", "folder", "pair"}.
    `entries` are the ticked tray rows in tray order: dicts with "path" and "kind" ("pdf" or "folder"). A folder row
    that lists its PDFs in the tray ("children" > 0) stands for them; the PDFs themselves are what gets used."""
    out = {"ok": False, "label": "", "problem": "", "paths": [], "folder": "", "pair": None, "button": "Use them here"}
    files = [e["path"] for e in entries if e.get("kind") == "pdf"]
    folders = [e["path"] for e in entries if e.get("kind") == "folder"]
    bare = [e["path"] for e in entries if e.get("kind") == "folder" and not e.get("children")]
    if not entries:
        out["problem"] = "Tick files in the tray to use them here."
        return out
    if verb == "merge":
        if len(files) != 2 or bare:
            out["problem"] = "Merge needs exactly two ticked PDFs. The first is updated with the pages of the second (a backup is kept)."
            return out
        out.update(ok=True, pair=(files[0], files[1]), paths=list(files), button="Merge these two",
                   label=f"Add the pages of {os.path.basename(files[1])} to {os.path.basename(files[0])}")
        return out
    if verb in ("split", "shrink", "pages"):
        if verb == "pages" and len(folders) == 1 and all(_is_under(f, folders[0]) for f in files):
            out.update(ok=True, folder=folders[0], button="Open in Page Organizer",
                       label=f"Open the folder {os.path.basename(folders[0]) or folders[0]} in the Page Organizer")
            return out
        pdfs = list(files)
        for f in bare:
            pdfs.extend(expand_folder_pdfs(f))
        seen, uniq = set(), []
        for p in pdfs:
            k = os.path.normcase(os.path.abspath(p))
            if k not in seen:
                seen.add(k)
                uniq.append(p)
        if not uniq:
            out["problem"] = "There are no PDFs in what is ticked."
            return out
        if verb == "split":
            label = f"Split {plural(len(uniq), 'PDF')}"
        elif verb == "shrink":
            label = (f"Optimize {plural(len(uniq), 'PDF')} now. Smaller copies are made in the output folder; "
                     "your originals are kept.")
        else:
            label = f"Open {plural(len(uniq), 'PDF')} in the Page Organizer"
        out.update(ok=True, paths=uniq, label=label,
                   button={"split": "Split them", "shrink": "Optimize now", "pages": "Open in Page Organizer"}[verb])
        return out
    if verb in ("rename", "move"):
        if len(folders) == 1 and all(_is_under(f, folders[0]) for f in files):
            out.update(ok=True, folder=folders[0], button=_FOLDER_BTN[verb],
                       label=_folder_label(verb, os.path.basename(folders[0]) or folders[0]))
            return out
        dirs = {os.path.dirname(os.path.abspath(p)) for p in files}
        if folders or len(dirs) != 1:
            out["problem"] = "This tool works on one folder. Tick files from a single folder, or a single folder."
            return out
        folder = dirs.pop()
        out.update(ok=True, folder=folder, button=_FOLDER_BTN[verb],
                   label=_folder_label(verb, os.path.basename(folder) or folder))
        return out
    out["problem"] = "Unknown tool."
    return out


ROLE_PARENT = Qt.UserRole + 1          # the folder row a listed PDF belongs to ("" for none)
ROLE_KIND = Qt.UserRole + 2            # "pdf" | "folder"


class _TrayList(QListWidget):
    """The tray's list. Rows drag to reorder inside it, and drag OUT as ordinary files (onto a tool's drop zone,
    Explorer, an e-mail). Files or folders dropped in from Explorer are added. Dragging never removes a row."""
    pathsDropped = Signal(object)
    reordered = Signal()
    openRequested = Signal(object)          # the row's item
    removeRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setUniformItemSizes(False)
        self.setStyleSheet("QListWidget::item { padding: 6px 4px; }")
        self.itemDoubleClicked.connect(self.openRequested.emit)

    def mimeData(self, items):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(it.data(Qt.UserRole)) for it in items])
        return md

    def _ticked_carry(self, items):
        """Grabbing a ticked row carries every ticked PDF (a folder row stands for the PDFs listed under it);
        grabbing an unticked row carries just what is selected."""
        if not any(it.checkState() != Qt.Unchecked for it in items):
            return items
        rows = [self.item(i) for i in range(self.count())]
        parents = {r.data(ROLE_PARENT) for r in rows if r.data(ROLE_PARENT)}
        out = [r for r in rows if r.checkState() == Qt.Checked and
               not (r.data(ROLE_KIND) == "folder" and r.data(Qt.UserRole) in parents)]
        return out or items

    def startDrag(self, supported):
        items = self.selectedItems()
        cur = self.currentItem()
        if not items and cur is not None:
            items = [cur]
        if not items:
            return
        items = self._ticked_carry(items)
        drag = QDrag(self)
        drag.setMimeData(self.mimeData(items))
        drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)   # the source is never emptied by a drop elsewhere

    def _urls(self, event):
        md = event.mimeData()
        return [u.toLocalFile() for u in md.urls() if u.toLocalFile()] if md.hasUrls() else []

    def dragEnterEvent(self, event):
        if self._urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def _drop_row(self, pos) -> int:
        it = self.itemAt(pos)
        if it is None:
            return self.count()
        row = self.row(it)
        return row + (1 if pos.y() > self.visualItemRect(it).center().y() else 0)

    def _block(self, parent: str):
        """Rows of the PDFs listed under the folder row `parent`."""
        return [r for r in range(self.count()) if self.item(r).data(ROLE_PARENT) == parent]

    def _grouped_move(self, rows, target):
        """Rows to move and where. A folder row takes its PDFs with it; PDFs moved alone stay inside their folder's
        block; nothing lands in the middle of another folder's block."""
        moving = set(rows)
        for r in list(moving):
            it = self.item(r)
            if it.data(ROLE_KIND) == "folder":
                moving.update(self._block(it.data(Qt.UserRole)))
        moving = sorted(moving)
        parents = {self.item(r).data(ROLE_PARENT) for r in moving}
        if len(parents) == 1 and next(iter(parents)):
            blk = self._block(next(iter(parents)))
            target = min(max(target, blk[0]), blk[-1] + 1)
        else:
            if 0 < target < self.count():
                above, below = self.item(target - 1).data(ROLE_PARENT), self.item(target).data(ROLE_PARENT)
                if above and above == below and above not in {self.item(r).data(ROLE_PARENT) for r in moving}:
                    target = self._block(above)[-1] + 1
        return moving, target

    def dropEvent(self, event):
        if event.source() is self:
            moving = sorted(self.row(it) for it in self.selectedItems())
            if moving:
                target = self._drop_row(event.position().toPoint())
                moving, target = self._grouped_move(moving, target)
                target -= sum(1 for r in moving if r < target)
                items = [self.takeItem(r) for r in reversed(moving)][::-1]
                for k, it in enumerate(items):
                    self.insertItem(target + k, it)
                    it.setSelected(True)
                self.reordered.emit()
            event.acceptProposedAction()
            return
        paths = self._urls(event)
        if paths:
            event.acceptProposedAction()
            self.pathsDropped.emit(paths)
        else:
            event.ignore()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete and self.selectedItems():
            self.removeRequested.emit()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentItem() is not None:
            self.openRequested.emit(self.currentItem())
            return
        super().keyPressEvent(event)


class TrayPanel(QFrame):
    """Files and folders waiting to be used. Ticked rows are what a tool receives; the row order is the order a
    tool gets them in (drag to change it). Nothing here modifies a file."""
    changed = Signal()
    openRequested = Signal(str, str, bool)  # path, kind ("pdf" | "folder"), as_b (compare)

    def __init__(self):
        super().__init__()
        self.setObjectName("panel")
        self.setAcceptDrops(True)
        self._info = {}                    # path -> {"path", "kind", "bytes", "pages"}
        self._token = 0
        self._workers = []
        self._shutting_down = False
        self._syncing = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("Tray")
        title.setObjectName("sectionTitle")
        head.addWidget(title)
        self.count_label = QLabel("empty")
        self.count_label.setObjectName("hint")
        head.addStretch(1)
        self.all_btn = QPushButton("All")
        self.all_btn.setObjectName("secondary")
        self.all_btn.setToolTip("Tick every file in the tray")
        self.all_btn.clicked.connect(_later(self._tick_all, True))
        self.none_btn = QPushButton("None")
        self.none_btn.setObjectName("secondary")
        self.none_btn.setToolTip("Untick every file in the tray")
        self.none_btn.clicked.connect(_later(self._tick_all, False))
        head.addWidget(self.all_btn)
        head.addWidget(self.none_btn)
        lay.addLayout(head)
        lay.addWidget(self.count_label)

        self.drop_hint = QLabel("Tick files to use them, drag rows to reorder, double-click to look in the Page Organizer.")
        self.drop_hint.setObjectName("hint")
        self.drop_hint.setWordWrap(True)
        lay.addWidget(self.drop_hint)

        row = QHBoxLayout()
        self.add_files_btn = QPushButton("Add files")
        self.add_files_btn.setToolTip("Choose PDFs to put in the tray")
        self.add_files_btn.clicked.connect(self.browse_files)
        self.add_folder_btn = QPushButton("Add folder")
        self.add_folder_btn.setObjectName("secondary")
        self.add_folder_btn.setToolTip("Put a whole folder in the tray (tools that work on folders use it as is)")
        self.add_folder_btn.clicked.connect(self.browse_folder)
        self.recent_btn = QPushButton("Recent ▾")
        self.recent_btn.setObjectName("secondary")
        self.recent_btn.setToolTip("Folders you added to the tray before")
        self.recent_btn.clicked.connect(self._show_recent)
        for b in (self.add_files_btn, self.add_folder_btn, self.recent_btn):
            row.addWidget(b)
        lay.addLayout(row)

        self.note = QLabel("")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        self.list = _TrayList()
        self.list.setAccessibleName("Files in the tray. Tick the ones to use; drag to reorder.")
        self.list.setToolTip("Tick the files to use. Drag a row to change the order.")
        self.list.itemChanged.connect(self._item_changed)
        self.list.reordered.connect(self._rows_moved)
        self.list.pathsDropped.connect(self.add_paths)
        self.list.openRequested.connect(self._open_item)
        self.list.removeRequested.connect(self.remove_selected)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._row_menu)
        lay.addWidget(self.list, stretch=1)

        self.empty_box = QLabel("Drop files here to reuse them across tools.\n\nDrag PDFs or a folder from Explorer and drop them "
                                "anywhere on this window, or press Add files.")
        self.empty_box.setObjectName("hint")
        self.empty_box.setWordWrap(True)
        self.empty_box.setAlignment(Qt.AlignCenter)
        self.empty_box.setMinimumHeight(120)
        lay.addWidget(self.empty_box, stretch=1)

        foot = QHBoxLayout()
        self.remove_btn = QPushButton("Remove ticked")
        self.remove_btn.setObjectName("secondary")
        self.remove_btn.setToolTip("Take the ticked rows out of the tray (the files stay where they are)")
        self.remove_btn.clicked.connect(self.remove_ticked)
        self.clear_btn = QPushButton("Clear tray")
        self.clear_btn.setObjectName("secondary")
        self.clear_btn.setToolTip("Empty the tray (the files stay where they are)")
        self.clear_btn.clicked.connect(self.clear)
        foot.addWidget(self.remove_btn)
        foot.addStretch(1)
        foot.addWidget(self.clear_btn)
        lay.addLayout(foot)
        self._sync_ui()

    # ---- model
    def paths(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def entries(self, ticked_only: bool = False):
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if ticked_only and it.checkState() != Qt.Checked:
                continue
            e = self._info.get(it.data(Qt.UserRole))
            if e is not None:
                d = dict(e)
                if d["kind"] == "folder":
                    d["children"] = len(self._kids(d["path"]))
                    d["pages"] = d["children"]
                out.append(d)
        return out

    def _kids(self, folder_path):
        return [i for i in range(self.list.count()) if self.list.item(i).data(ROLE_PARENT) == folder_path]

    def ticked(self):
        return self.entries(ticked_only=True)

    @staticmethod
    def _key(path):
        return os.path.normcase(os.path.abspath(path))

    def _row_text(self, e) -> str:
        name = os.path.basename(e["path"].rstrip("\\/")) or e["path"]
        if e["kind"] == "folder":
            n = len(self._kids(e["path"]))
            meta = ("Folder · unreadable" if e.get("unreadable") else
                    "Folder · " + (plural(n, "PDF") + " listed below" if n else "no PDFs directly inside"))
        else:
            n = e.get("pages")
            bits = []
            if n is not None:
                bits.append("not readable" if n < 0 else plural(n, "page"))
            bits.append(human_size(e.get("bytes", 0)))
            if e.get("bytes", 0) >= TRAY_LARGE_BYTES:
                bits.append("large: Optimize?")
            meta = " · ".join(bits)
        indent = "      " if e.get("parent") else ""
        return f"{indent}{name}\n{indent}{meta}"

    def _refresh_row(self, path):
        e = self._info.get(path)
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == path and e is not None:
                self.list.blockSignals(True)
                it.setText(self._row_text(e))
                self.list.blockSignals(False)
                return

    def _make_item(self, e):
        it = QListWidgetItem(self._row_text(e))
        it.setData(Qt.UserRole, e["path"])
        it.setData(ROLE_PARENT, e.get("parent") or "")
        it.setData(ROLE_KIND, e["kind"])
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
        it.setCheckState(Qt.Checked)
        if e["kind"] == "folder":
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            it.setIcon(self.style().standardIcon(QStyle.SP_DirIcon))
        return it

    def _insert_after_block(self, folder_path, it):
        last = -1
        for i in range(self.list.count()):
            row = self.list.item(i)
            if row.data(Qt.UserRole) == folder_path or row.data(ROLE_PARENT) == folder_path:
                last = i
        self.list.insertItem(last + 1 if last >= 0 else self.list.count(), it)

    def add_paths(self, paths) -> str:
        """Put files and folders in the tray (ticked). A folder is listed with the PDFs directly inside it, so they
        can be used one by one straight away. Returns the one-line summary also shown under the buttons."""
        raw = [os.path.abspath(p) for p in (paths or []) if isinstance(p, str) and p]
        folders = [p for p in raw if os.path.isdir(p)]
        files = [p for p in raw if not os.path.isdir(p)]
        info = classify_drop(files)
        good = {self._key(p): p for p in info["pdfs"]}
        stored = {self._key(p): p for p in self.paths()}
        added_pdf, added_dir, dup, over, size, subdirs, empty, unreadable = [], [], 0, 0, 0, 0, [], []
        new_files = []
        self.list.blockSignals(True)

        def add_pdf(path, parent=""):
            nonlocal dup, over, size
            k = self._key(path)
            if k in stored:
                dup += 1
                return
            if self.list.count() >= TRAY_MAX:
                over += 1
                return
            try:
                nbytes = os.path.getsize(path)
            except OSError:
                nbytes = 0
            e = {"path": path, "kind": "pdf", "bytes": nbytes, "pages": None, "parent": parent}
            self._info[path] = e
            stored[k] = path
            it = self._make_item(e)
            if parent:
                self._insert_after_block(parent, it)
            else:
                self.list.addItem(it)
            added_pdf.append(path)
            new_files.append(path)
            size += nbytes

        for p in raw:
            if p in folders:
                k = self._key(p)
                header = stored.get(k)
                if header is None:
                    if self.list.count() >= TRAY_MAX:
                        over += 1
                        continue
                    e = {"path": p, "kind": "folder", "bytes": 0, "pages": None, "parent": ""}
                    self._info[p] = e
                    stored[k] = header = p
                    self.list.addItem(self._make_item(e))
                    added_dir.append(p)
                    push_recent("tray_folders", p)
                kids, nsub, readable = folder_pdf_listing(header)
                subdirs += nsub
                if not readable:
                    self._info[header]["unreadable"] = True
                    unreadable.append(os.path.basename(header) or header)
                elif not kids:
                    empty.append(os.path.basename(header) or header)
                for kid in kids:
                    add_pdf(kid, header)
            elif self._key(p) in good:
                add_pdf(p)
        self._refresh_all_headers()
        self.list.blockSignals(False)
        parts = []
        if added_pdf or added_dir:
            what = []
            if added_dir:
                what.append(plural(len(added_dir), "folder"))
            if added_pdf:
                what.append(plural(len(added_pdf), "PDF"))
            joiner = " with " if len(added_dir) == 1 and added_pdf and not files else " and "
            parts.append("Added " + joiner.join(what) + (f" · {human_size(size)}" if added_pdf else "") + ".")
        else:
            parts.append("Nothing was added.")
        if dup:
            parts.append(f"{plural(dup, 'item')} already in the tray.")
        if over:
            parts.append(f"The tray holds {TRAY_MAX} at most; {plural(over, 'item')} not added.")
        if empty:
            parts.append("No PDFs directly inside: " + ", ".join(empty[:3]) + ".")
        if unreadable:
            parts.append("Could not read the folder: " + ", ".join(unreadable[:3]) + ".")
        if subdirs:
            parts.append(f"{plural(subdirs, 'sub-folder')} not searched; add them too if you need them.")
        skipped = [(n, w) for n, w in info["skipped"] if n not in {os.path.basename(f) for f in folders}]
        if skipped:
            shown = ", ".join(f"{n} ({w})" for n, w in skipped[:4])
            more = len(skipped) - 4
            parts.append(f"Skipped {len(skipped)}: {shown}" + (f" and {more} more" if more > 0 else "") + ".")
        text = " ".join(parts)
        self.note.setText(text)
        self._pump_workers(new_files)
        self._sync_ui()
        self.changed.emit()
        return text

    def _refresh_all_headers(self):
        """Folder rows show how many of their PDFs are listed and tick as a whole (partly ticked when some are)."""
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(ROLE_KIND) != "folder":
                continue
            path = it.data(Qt.UserRole)
            kids = [self.list.item(k).checkState() for k in self._kids(path)]
            if kids:
                state = (Qt.Checked if all(k == Qt.Checked for k in kids) else
                         Qt.Unchecked if all(k == Qt.Unchecked for k in kids) else Qt.PartiallyChecked)
                it.setCheckState(state)
            e = self._info.get(path)
            if e is not None:
                it.setText(self._row_text(e))

    def _pump_workers(self, files):
        if self._shutting_down:
            return
        self._workers = [w for w in self._workers if self._alive(w)]
        if files:
            w = _InfoWorker(files, self._token)
            w.info.connect(self._on_pages)
            self._workers.append(w)
            w.start()

    @staticmethod
    def _alive(w) -> bool:
        try:
            return w.isRunning()
        except RuntimeError:
            return False

    def _on_pages(self, token, path, n):
        if token != self._token or path not in self._info:
            return
        self._info[path]["pages"] = n
        self._refresh_row(path)

    # ---- actions
    def _tick_all(self, on: bool):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.list.blockSignals(False)
        self._sync_ui()
        self.changed.emit()

    def remove_ticked(self):
        self.list.blockSignals(True)
        for i in range(self.list.count() - 1, -1, -1):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                self._info.pop(it.data(Qt.UserRole), None)
                self.list.takeItem(i)
        self._refresh_all_headers()
        self.list.blockSignals(False)
        self.note.setText("")
        self._sync_ui()
        self.changed.emit()

    def clear(self):
        self._token += 1                    # late page counts for the old rows are dropped
        for w in self._workers:
            if self._alive(w):
                w.cancel()
        self.list.blockSignals(True)
        self.list.clear()
        self.list.blockSignals(False)
        self._info.clear()
        self.note.setText("")
        self._sync_ui()
        self.changed.emit()

    def browse_files(self):
        start = (recent_paths("tray_folders") or [""])[0]
        files, _ = QFileDialog.getOpenFileNames(self, "Choose PDFs for the tray", start, "PDF Files (*.pdf)")
        if files:
            self.add_paths(files)

    def browse_folder(self):
        start = (recent_paths("tray_folders") or [""])[0]
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder for the tray", start)
        if folder:
            self.add_paths([folder])

    def _show_recent(self):
        items = [p for p in recent_paths("tray_folders") if os.path.isdir(p)]
        menu = QMenu(self)
        if not items:
            menu.addAction("No recent folders").setEnabled(False)
        for p in items:
            menu.addAction(p, _later(self.add_paths, [p]))
        menu.exec(self.recent_btn.mapToGlobal(self.recent_btn.rect().bottomLeft()))
        menu.deleteLater()

    # ---- opening, menu, drops on the panel itself
    def _item_path(self, item):
        return item.data(Qt.UserRole) if item is not None else None

    def _open_item(self, item, as_b: bool = False):
        path = self._item_path(item)
        e = self._info.get(path)
        if e is not None:
            self.openRequested.emit(path, e["kind"], bool(as_b))

    def remove_selected(self):
        rows = {self.list.row(it) for it in self.list.selectedItems()}
        for r in list(rows):                                  # a folder row goes with the PDFs listed under it
            if self.list.item(r).data(ROLE_KIND) == "folder":
                rows.update(self._kids(self.list.item(r).data(Qt.UserRole)))
        rows = sorted(rows, reverse=True)
        if not rows:
            return
        self.list.blockSignals(True)
        for r in rows:
            self._info.pop(self.list.item(r).data(Qt.UserRole), None)
            self.list.takeItem(r)
        self._refresh_all_headers()
        self.list.blockSignals(False)
        self.note.setText("")
        self._sync_ui()
        self.changed.emit()

    def tick_only(self, item):
        self.list.blockSignals(True)
        keep = {item.data(Qt.UserRole)}
        if item.data(ROLE_KIND) == "folder":
            keep.update(self.list.item(i).data(Qt.UserRole) for i in self._kids(item.data(Qt.UserRole)))
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in keep else Qt.Unchecked)
        self._refresh_all_headers()
        self.list.blockSignals(False)
        self._sync_ui()
        self.changed.emit()

    def _row_menu(self, pos):
        menu = self._build_row_menu(pos)
        if menu is not None:
            menu.exec(self.list.viewport().mapToGlobal(pos))
            menu.deleteLater()

    def _build_row_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return None
        if not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)
        path = self._item_path(item)
        e = self._info.get(path)
        if e is None:
            return None
        menu = QMenu(self)
        menu.addAction("Open in the Page Organizer" if e["kind"] == "pdf" else "Open this folder in the Page Organizer",
                       _later(self._open_item, item))
        if e["kind"] == "pdf":
            menu.addAction("Compare: show it in B beside another PDF", _later(self._open_item, item, True))
        paths = [self._item_path(i) for i in self.list.selectedItems()] or [path]
        menu.addMenu(build_send_menu(self, "tray", paths, "Send to"))
        menu.addAction("Show in its folder", _later(self._show_in_folder, path))
        menu.addAction("Tick only this one", _later(self.tick_only, item))
        menu.addSeparator()
        menu.addAction("Remove from the tray (the file stays where it is)", _later(self.remove_selected))
        return menu

    @staticmethod
    def _show_in_folder(path):
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    def _item_changed(self, item):
        if self._syncing:
            return
        self._syncing = True
        try:
            if item.data(ROLE_KIND) == "folder":
                state = item.checkState()
                if state != Qt.PartiallyChecked:           # ticking a folder row ticks every PDF listed under it
                    for k in self._kids(item.data(Qt.UserRole)):
                        self.list.item(k).setCheckState(state)
            self._refresh_all_headers()
        finally:
            self._syncing = False
        self._sync_ui()
        self.changed.emit()

    def _rows_moved(self, *_a):
        self.changed.emit()

    def _sync_ui(self):
        n = self.list.count()
        rows = [self.list.item(i) for i in range(n)]
        pdfs = [r for r in rows if r.data(ROLE_KIND) == "pdf"]
        dirs = [r for r in rows if r.data(ROLE_KIND) == "folder"]
        ticked = sum(1 for r in pdfs if r.checkState() == Qt.Checked) + \
            sum(1 for r in dirs if r.checkState() == Qt.Checked and not self._kids(r.data(Qt.UserRole)))
        size = sum(self._info[r.data(Qt.UserRole)].get("bytes", 0) for r in pdfs if r.data(Qt.UserRole) in self._info)
        shown = plural(len(pdfs), "PDF") + (f" in {plural(len(dirs), 'folder')}" if dirs else "")
        self.count_label.setText("empty" if not n else f"{shown} · {ticked} ticked · {human_size(size)}")
        self.list.setVisible(n > 0)
        self.empty_box.setVisible(n == 0)
        self.drop_hint.setVisible(n > 0)
        for b in (self.all_btn, self.none_btn, self.clear_btn):
            b.setEnabled(n > 0)
        self.remove_btn.setEnabled(any(r.checkState() != Qt.Unchecked for r in rows))

    def stop_for_shutdown(self, wait_ms: int = 5000) -> bool:
        self._shutting_down = True
        ok = True
        for w in self._workers:
            if self._alive(w):
                w.cancel()
                ok = w.wait(wait_ms) and ok
        return ok


class TrayShell(QWidget):
    """The Tray layout around the tab widget: tray | tool buttons + the active tool."""

    def __init__(self):
        super().__init__()
        self._tabs = None
        self.mode = "tray"
        wanted = load_ui_state().get("tray_visible", False)
        self._tray_wanted = wanted is True              # hidden until the person asks for it, then remembered
        self._flash = ""
        self.setAcceptDrops(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = self.top_bar = QFrame()
        bar.setObjectName("topBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 0, 16, 0)
        bl.setSpacing(6)
        brand = self.brand = QWidget()
        brand.setObjectName("brand")
        brl = QHBoxLayout(brand)
        brl.setContentsMargins(0, 0, 14, 0)
        brl.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(_svg_pixmap(APP_ICON_SVG, 26))
        mark.setAccessibleName("Acro-Data")
        brl.addWidget(mark)
        name = QLabel("Acro-Data")
        name.setObjectName("brandName")
        brl.addWidget(name)
        bl.addWidget(brand)
        self.verbs_w = QWidget()
        verbs = QHBoxLayout(self.verbs_w)
        verbs.setContentsMargins(0, 0, 0, 0)
        verbs.setSpacing(2)
        self.verb_buttons = {}
        for key, label, sub, icon_key, title in TRAY_VERBS:
            b = QPushButton(label)
            b.setObjectName("verbCard")
            b.setCheckable(True)
            b.setAutoExclusive(False)
            b.setIcon(tab_icon(icon_key))
            b.setIconSize(QSize(18, 18))
            b.setToolTip(f"{title}: {sub}")
            b.setMinimumHeight(52)
            b.setAccessibleName(f"{label}: {sub}")
            b.clicked.connect(_later(self._pick, key))
            verbs.addWidget(b)
            self.verb_buttons[key] = b
        # Two rounded pills glide behind the tool buttons: a tinted one under
        # the current tool, a fainter one that follows the pointer. They are
        # drawn by the shell (not the buttons) so they can animate.
        self._pill_sel = QFrame(self.verbs_w)
        self._pill_sel.setObjectName("pillSel")
        self._pill_hov = QFrame(self.verbs_w)
        self._pill_hov.setObjectName("pillHover")
        for pl in (self._pill_sel, self._pill_hov):
            pl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            pl.lower()
            pl.hide()
        self._pill_anim = {}
        self.verbs_w.installEventFilter(self)
        for b_ in self.verb_buttons.values():
            b_.installEventFilter(self)
        bl.addWidget(self.verbs_w)
        bl.addStretch(1)
        self.tray_toggle = QPushButton("Show file tray")
        self.tray_toggle.setObjectName("secondary")
        self.tray_toggle.setToolTip("Show or hide the file tray: a waiting list of PDFs and folders for the tools (Ctrl+T)")
        self.tray_toggle.clicked.connect(self.toggle_tray)
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setObjectName("topGhost")
        self.settings_btn.setToolTip("Open Settings (the layout can be switched back to the classic tabs there)")
        self.settings_btn.clicked.connect(_later(self._open_tab, "Settings"))
        self.help_btn = QPushButton("Help")
        self.help_btn.setObjectName("topGhost")
        self.help_btn.setToolTip("Open Help")
        self.help_btn.clicked.connect(_later(self._open_tab, "Help"))
        for w_ in (self.tray_toggle, self.settings_btn, self.help_btn):
            bl.addWidget(w_)
        root.addWidget(bar)

        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.tray = TrayPanel()
        self.tray.setMinimumWidth(250)
        self.tray.changed.connect(self._tray_changed)
        self.tray.openRequested.connect(self._open_row)
        self.split.addWidget(self.tray)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(14, 8, 14, 0)
        rl.setSpacing(8)
        self.right_layout = rl
        self.heading = QLabel("")
        self.heading.setObjectName("pathLabel")
        rl.addWidget(self.heading)

        self.feed = QFrame()
        self.feed.setObjectName("panel")
        fl = QHBoxLayout(self.feed)
        fl.setContentsMargins(12, 8, 12, 8)
        self.feed_label = QLabel("")
        self.feed_label.setWordWrap(True)
        fl.addWidget(self.feed_label, stretch=1)
        self.feed_btn = QPushButton("Use them here")
        self.feed_btn.setObjectName("primaryAction")
        self.feed_btn.setToolTip("Hand the ticked tray rows to this tool. Its usual confirmation still appears before anything is written.")
        self.feed_btn.clicked.connect(self._use)
        fl.addWidget(self.feed_btn)
        rl.addWidget(self.feed)

        self.work = QVBoxLayout()
        self.work.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(self.work, stretch=1)
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([330, 1000])
        root.addWidget(self.split, stretch=1)
        self.restyle()
        self.refresh()

    # ---- wiring to the tab widget
    def attach_tabs(self, tabs):
        if self._tabs is tabs:
            return
        self._tabs = tabs
        self.work.addWidget(tabs)
        tabs.currentChanged.connect(self.refresh)
        self.refresh()

    def set_mode(self, mode: str):
        """'tray': tray, tool buttons and the active tool. 'classic': only the tabs, with their own strip.
        The tab widget never moves; reparenting a hidden strip loses its density styling."""
        tray = mode == "tray"
        self.top_bar.setVisible(tray)
        self.tray.setVisible(tray and self._tray_wanted)
        self.verbs_w.setVisible(tray)
        if self._tabs is not None:
            self._tabs.tabBar().setVisible(not tray)
        self.right_layout.setContentsMargins(14, 8, 14, 0) if tray else self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.mode = mode
        self.refresh()
        if self._tabs is not None and not tray:
            self._tabs.tabBar()._fit()

    def _index_of(self, title: str) -> int:
        tabs = self._tabs
        if tabs is None:
            return -1
        for i in range(tabs.count()):
            if tabs.tabBar().title(i) == title:
                return i
        return -1

    def _current_verb(self):
        tabs = self._tabs
        if tabs is None:
            return None
        title = tabs.tabBar().title(tabs.currentIndex())
        for key, _l, _s, _i, t in TRAY_VERBS:
            if t == title:
                return key
        return None

    def _open_tab(self, title: str):
        i = self._index_of(title)
        if i >= 0:
            self._tabs.setCurrentIndex(i)

    def _pick(self, key: str):
        for k, _l, _s, _i, title in TRAY_VERBS:
            if k == key:
                self._open_tab(title)
        self.refresh()

    def toggle_tray(self):
        if self.mode != "tray":
            return
        self._set_tray_wanted(not self._tray_wanted)

    def _set_tray_wanted(self, show: bool):
        self._tray_wanted = show
        self.tray.setVisible(show and self.mode == "tray")
        try:
            update_ui_state(tray_visible=show)
        except Exception:
            pass
        self.refresh()
        QTimer.singleShot(0, _later(self._room_changed))

    def hide_tray_for_session(self):
        """Fold the tray away without saving that as the person's choice (the next start restores it)."""
        if self._tray_wanted:
            self._tray_wanted = False
            self.tray.setVisible(False)
            self.refresh()
            QTimer.singleShot(0, _later(self._room_changed))

    def _room_changed(self):
        fn = getattr(self.window(), "sync_actions_room", None)
        if callable(fn):
            fn()

    def _tray_changed(self):
        self._flash = ""
        self.refresh()

    def _open_row(self, path, kind, as_b=False):
        win = self.window()
        fn = getattr(win, "open_from_tray", None)
        if callable(fn):
            ok, msg = fn(path, kind, as_b)
            self._flash = msg or ""
            self._flash_verb = None
            self.refresh()

    def feed_plan(self):
        verb = self._current_verb()
        return verb, (plan_tray_feed(verb, self.tray.ticked()) if verb else None)

    def refresh(self, *_a):
        verb = self._current_verb()
        for key, b in self.verb_buttons.items():
            b.blockSignals(True)
            b.setChecked(key == verb)
            b.blockSignals(False)
        self._sync_sel_pill()
        ticked = self.tray.ticked()
        n = len(ticked)
        held = len(self.tray.paths())
        self.tray_toggle.setText(("Hide file tray" if self._tray_wanted else
                                  "Show file tray" + (f" ({held} waiting)" if held else "")))
        info = next(((l, sb) for k, l, sb, _i, _t in TRAY_VERBS if k == verb), None)
        if n and self._tray_wanted:
            self.heading.setText(f"{info[0]}: {info[1]}. {n} ticked in the tray." if info else
                                 f"{n} ticked in the tray.")
        elif info:
            self.heading.setText(f"{info[0]}: {info[1]}")
        else:
            self.heading.setText("")
        if self._flash and getattr(self, "_flash_verb", verb) not in (verb, None):
            self._flash = ""                    # a message about another tool is stale here
        if self._flash and self.mode == "tray":
            self.feed.setVisible(True)
            self.feed_label.setText(self._flash)
            self.feed_btn.setVisible(False)
            return
        self.feed_btn.setVisible(True)
        if self.mode != "tray" or verb is None or not ticked or not self._tray_wanted:
            self.feed.setVisible(False)
            return
        plan = plan_tray_feed(verb, ticked)
        self.feed.setVisible(True)
        self.feed_label.setText(plan["label"] if plan["ok"] else plan["problem"])
        self.feed_btn.setEnabled(plan["ok"])
        self.feed_btn.setText(plan.get("button") or "Use them here")

    def _use(self):
        win = self.window()
        fn = getattr(win, "feed_from_tray", None)
        verb = self._current_verb()
        if not callable(fn) or verb is None:
            return
        ok, msg = fn(verb)
        self._flash = msg or ""
        self._flash_verb = verb
        self.refresh()

    # ---- drops anywhere that no tool claims
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.tray.add_paths(paths)
            if self.mode == "tray" and not self._tray_wanted:
                self._set_tray_wanted(True)

    # ---- gliding pills ---------------------------------------------------
    def _pill_rect(self, btn):
        g = btn.geometry()
        return QRect(g.x(), (self.verbs_w.height() - 36) // 2, g.width(), 36)

    def _glide(self, pill, rect, animate=True):
        key = pill.objectName()
        old = self._pill_anim.pop(key, None)
        if old is not None:
            old.stop()
        if not pill.isVisible() or not animate:
            pill.setGeometry(rect)
            pill.show()
            pill.lower()
            return
        a = QPropertyAnimation(pill, b"geometry", self)
        a.setDuration(160)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.setStartValue(pill.geometry())
        a.setEndValue(rect)
        self._pill_anim[key] = a
        a.start()

    def _sync_sel_pill(self, animate=True):
        cur = next((b for b in self.verb_buttons.values() if b.isChecked()), None)
        if cur is None or not self.verbs_w.isVisible():
            self._pill_sel.hide()
            return
        self._glide(self._pill_sel, self._pill_rect(cur), animate)

    def eventFilter(self, obj, event):
        et = event.type()
        if obj is self.verbs_w:
            if et in (QEvent.Resize, QEvent.Show):
                QTimer.singleShot(0, lambda: self._sync_sel_pill(False))
            elif et == QEvent.Leave:
                self._pill_hov.hide()
        elif obj in self.verb_buttons.values():
            if et == QEvent.Enter and not obj.isChecked():
                self._glide(self._pill_hov, self._pill_rect(obj))
            elif et == QEvent.Enter:
                self._pill_hov.hide()
        return super().eventFilter(obj, event)

    def restyle(self):
        c = theme_color
        self.setStyleSheet(
            f"QFrame#topBar {{ background:{c('panel')}; border:none; border-bottom:1px solid {c('grid')}; border-radius:0; }}"
            f"QLabel#brandName {{ font-size:15px; font-weight:700; color:{c('text')}; }}"
            f"QWidget#brand {{ margin-right:10px; }}"
            f"QPushButton#verbCard {{ background:transparent; color:{c('muted')}; border:none;"
            f" border-radius:18px; padding:0 14px; font-weight:600; }}"
            f"QPushButton#verbCard:hover {{ color:{c('text')}; background:transparent; }}"
            f"QPushButton#verbCard:checked {{ color:{c('accent')}; background:transparent; }}"
            f"QFrame#pillSel {{ background:{_rgba(c('accent'), 0.14)}; border:none; border-radius:18px; }}"
            f"QFrame#pillHover {{ background:{_rgba(c('text'), 0.07)}; border:none; border-radius:18px; }}"
            f"QPushButton#topGhost {{ background:transparent; color:{c('muted')}; border:none; border-radius:16px; padding:6px 14px; font-weight:600; }}"
            f"QPushButton#topGhost:hover {{ background:{_rgba(c('text'), 0.07)}; color:{c('text')}; }}")
        for key, label, sub, icon_key, _t in TRAY_VERBS:
            self.verb_buttons[key].setIcon(tab_icon(icon_key))


# =====================================================================
# Default PDF app (per user, no administrator rights) and the first-run guide
# =====================================================================
# Windows 10/11 will not let a program silently take over a file type: the person
# has to confirm the choice themselves. So "make it the default" here means two
# honest steps: register Acro-Data as an app that can open PDFs (HKEY_CURRENT_USER
# only, fully reversible), then open Windows' Default apps page for the final click.
PDF_PROGID = "AcroD.PDF"
_REG_APP = r"Software\Classes\Applications\AcroD.exe"
_REG_PROG = r"Software\Classes\AcroD.PDF"
_REG_CAPS = r"Software\AcroD\Capabilities"
_REG_OPENWITH = r"Software\Classes\.pdf\OpenWithProgids"
_REG_REGAPPS = r"Software\RegisteredApplications"


class _WinReg:
    """HKEY_CURRENT_USER through winreg. Tests pass a stand-in with the same four methods."""

    def __init__(self):
        import winreg
        self._w = winreg

    def set(self, path, name, value):
        w = self._w
        with w.CreateKeyEx(w.HKEY_CURRENT_USER, path, 0, w.KEY_SET_VALUE) as k:
            w.SetValueEx(k, name, 0, w.REG_SZ, value)

    def get(self, path, name):
        w = self._w
        try:
            with w.OpenKey(w.HKEY_CURRENT_USER, path) as k:
                return w.QueryValueEx(k, name)[0]
        except OSError:
            return None

    def delete_value(self, path, name):
        w = self._w
        try:
            with w.OpenKey(w.HKEY_CURRENT_USER, path, 0, w.KEY_SET_VALUE) as k:
                w.DeleteValue(k, name)
        except OSError:
            pass

    def delete_tree(self, path):
        w = self._w
        try:
            with w.OpenKey(w.HKEY_CURRENT_USER, path, 0, w.KEY_ALL_ACCESS) as k:
                while True:
                    try:
                        sub = w.EnumKey(k, 0)
                    except OSError:
                        break
                    self.delete_tree(path + "\\" + sub)
            w.DeleteKey(w.HKEY_CURRENT_USER, path)
        except OSError:
            pass


def pdf_handler_entries(exe: str) -> list:
    """(key path, value name, value) rows that make Acro-Data an app Windows can offer for PDFs."""
    cmd = f'"{exe}" "%1"'
    return [
        (_REG_PROG, "", "PDF document (Acro-Data)"),
        (_REG_PROG + r"\DefaultIcon", "", f'"{exe}",0'),
        (_REG_PROG + r"\shell\open\command", "", cmd),
        (_REG_APP, "FriendlyAppName", APP_NAME),
        (_REG_APP + r"\shell\open\command", "", cmd),
        (_REG_APP + r"\SupportedTypes", ".pdf", ""),
        (_REG_CAPS, "ApplicationName", APP_NAME),
        (_REG_CAPS, "ApplicationDescription", "Merge, split, shrink, rename and reorganize PDFs. Everything stays on your computer."),
        (_REG_CAPS + r"\FileAssociations", ".pdf", PDF_PROGID),
        (_REG_OPENWITH, PDF_PROGID, ""),
        (_REG_REGAPPS, "AcroD", _REG_CAPS),
    ]


def pdf_handler_available(exe=None) -> bool:
    """Only an installed Windows .exe can register itself: from source there is no exe to point Windows at."""
    return sys.platform == "win32" and bool(exe or getattr(sys, "frozen", False))


def register_pdf_handler(exe=None, reg=None) -> tuple:
    """(ok, message). Writes only under HKEY_CURRENT_USER; nothing else on the computer changes."""
    exe = exe or sys.executable
    try:
        reg = reg or _WinReg()
        for path, name, value in pdf_handler_entries(exe):
            reg.set(path, name, value)
    except Exception as e:                                   # a blocked registry must never stop the app
        return False, f"Windows would not let Acro-Data register itself ({e.__class__.__name__}). Nothing was changed."
    _notify_shell()
    return True, "Acro-Data is registered as an app that can open PDFs."


def unregister_pdf_handler(reg=None) -> tuple:
    try:
        reg = reg or _WinReg()
        reg.delete_tree(_REG_PROG)
        reg.delete_tree(_REG_APP)
        reg.delete_tree(r"Software\AcroD")
        reg.delete_value(_REG_OPENWITH, PDF_PROGID)
        reg.delete_value(_REG_REGAPPS, "AcroD")
    except Exception as e:
        return False, f"Could not remove the PDF registration ({e.__class__.__name__})."
    _notify_shell()
    return True, "Acro-Data's PDF registration was removed."


def pdf_handler_registered(exe=None, reg=None) -> bool:
    try:
        reg = reg or _WinReg()
        return reg.get(_REG_PROG + r"\shell\open\command", "") == f'"{exe or sys.executable}" "%1"'
    except Exception:
        return False


def _notify_shell():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # SHCNE_ASSOCCHANGED
    except Exception:
        pass


def open_default_apps_page() -> bool:
    return QDesktopServices.openUrl(QUrl("ms-settings:defaultapps"))


def log_startup(note: str) -> None:
    """One line per start in startup.log (kept short): which build ran and why the guide did or did not open."""
    try:
        path = os.path.join(_log_dir(), "startup.log")
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = f.read().splitlines()[-40:]
        except OSError:
            old = []
        old.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {APP_NAME} {APP_VERSION}  "
                   f"{'exe' if getattr(sys, 'frozen', False) else 'source'}  {note}")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(old) + "\n")
    except Exception:
        pass


def first_run_pending() -> bool:
    """True until the guide has been seen once (finished or skipped). An upgrade with settings already on disk sees
    it once too: it is where the new full-screen reader and default-PDF choices are explained."""
    return load_ui_state().get("first_run_done") is not True


def mark_first_run_done() -> None:
    update_ui_state(first_run_done=True)


class FirstRunDialog(QDialog):
    """Four short pages: welcome, look, default PDF app, done. Every page can be skipped and Cancel
    leaves everything as it was (the look is previewed live and put back)."""

    PAGES = ("Welcome", "Look", "PDFs", "Ready")

    def __init__(self, parent=None, register=None, open_settings_page=None):
        super().__init__(parent)
        self.setWindowTitle(f"Welcome to {APP_NAME}")
        self.setModal(True)
        self.setMinimumSize(580, 400)
        self._register = register or register_pdf_handler
        self._open_page = open_settings_page or open_default_apps_page
        self._orig_theme = load_ui_state().get("theme", DEFAULT_THEME)
        self._orig_side = setting("actions_side")
        self._done = False
        self.result_note = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(14)
        self.step = QLabel("")
        self.step.setObjectName("hint")
        root.addWidget(self.step)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # 1. welcome
        w = QWidget(); l = QHBoxLayout(w); l.setSpacing(18)
        pm = QLabel(); pm.setPixmap(mascot_pixmap("happy", 96)); pm.setAlignment(Qt.AlignTop)
        l.addWidget(pm)
        t = QVBoxLayout()
        h = QLabel(f"Welcome to {APP_NAME}"); h.setObjectName("sectionTitle")
        b = QLabel("Merge, split, shrink, rename and reorganize PDFs.\n\nEverything happens on this computer; "
                   "nothing is uploaded. Your original files are never changed without a backup.\n\n"
                   "Two quick choices follow. Each one can be skipped and changed later in Settings.")
        b.setWordWrap(True)
        t.addWidget(h); t.addWidget(b); t.addStretch(1)
        l.addLayout(t, 1)
        self.stack.addWidget(w)

        # 2. look
        w = QWidget(); g = QGridLayout(w); g.setVerticalSpacing(10)
        h = QLabel("Pick a look"); h.setObjectName("sectionTitle")
        g.addWidget(h, 0, 0, 1, 4)
        self.theme_buttons = {}
        self._theme_group = QButtonGroup(self)
        row = QHBoxLayout()
        row.setSpacing(18)
        for name in THEMES:
            rb = QRadioButton(name); rb.setChecked(name == self._orig_theme)
            rb.setToolTip(f"Use the {name} theme")
            self._theme_group.addButton(rb); self.theme_buttons[name] = rb; row.addWidget(rb)
            rb.toggled.connect(_later(self._preview_theme, name))
        row.addStretch(1)
        g.addWidget(QLabel("Theme"), 1, 0); g.addLayout(row, 1, 1, 1, 3)
        self.side_buttons = {}
        self._side_group = QButtonGroup(self)
        row = QHBoxLayout()
        row.setSpacing(18)
        for key, text in (("right", "Right"), ("left", "Left")):
            rb = QRadioButton(text); rb.setChecked(key == self._orig_side)
            rb.setToolTip(f"Put each tool's Actions column on the {key}")
            self._side_group.addButton(rb); self.side_buttons[key] = rb; row.addWidget(rb)
            rb.toggled.connect(_later(self._preview_side, key))
        row.addStretch(1)
        g.addWidget(QLabel("Actions column"), 2, 0); g.addLayout(row, 2, 1, 1, 3)
        n = QLabel("The window behind this box changes as you choose.")
        n.setObjectName("hint"); g.addWidget(n, 3, 0, 1, 4)
        g.setRowStretch(4, 1)
        self.stack.addWidget(w)

        # 3. default PDF app
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(10)
        h = QLabel("Open your PDFs here"); h.setObjectName("sectionTitle"); v.addWidget(h)
        self.can_register = pdf_handler_available()
        txt = QLabel("Make Acro-Data the app that opens PDFs, so a double-click in Explorer shows the file in the "
                     "Page Organizer's full-screen reader.\n\nWindows asks you to confirm this choice yourself. "
                     "Acro-Data registers itself (for your account only, no administrator needed) and then opens "
                     "Windows' Default apps page: pick Acro-Data for PDF there.")
        txt.setWordWrap(True); v.addWidget(txt)
        self.pdf_box = QCheckBox("Make Acro-Data my PDF app")
        self.pdf_box.setToolTip("Registers Acro-Data for your account and opens Windows' Default apps page for the final click")
        self.pdf_box.setChecked(self.can_register)
        self.pdf_box.setEnabled(self.can_register)
        v.addWidget(self.pdf_box)
        if not self.can_register:
            note = QLabel("This is only available in the installed Windows program. You can do it later from Settings.")
            note.setObjectName("hint"); note.setWordWrap(True); v.addWidget(note)
        v.addStretch(1)
        self.stack.addWidget(w)

        # 4. ready
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(10)
        h = QLabel("You're set"); h.setObjectName("sectionTitle"); v.addWidget(h)
        self.ready_text = QLabel(""); self.ready_text.setWordWrap(True); v.addWidget(self.ready_text)
        tips = QLabel("• Pick a tool along the top. Its buttons live in the Actions column.\n"
                      "• Ctrl+J folds the Actions column away; Ctrl+T shows the file tray.\n"
                      "• Help has answers to the common questions, and Settings can run this guide again.")
        tips.setWordWrap(True); v.addWidget(tips); v.addStretch(1)
        self.stack.addWidget(w)

        bar = QHBoxLayout()
        self.skip_btn = QPushButton("Skip setup"); self.skip_btn.setObjectName("secondary")
        self.skip_btn.setToolTip("Close this guide and keep everything as it is")
        self.back_btn = QPushButton("Back"); self.back_btn.setObjectName("secondary")
        self.back_btn.setToolTip("Go to the previous page")
        self.next_btn = QPushButton("Next"); self.next_btn.setToolTip("Go to the next page")
        self.skip_btn.clicked.connect(self.reject)
        self.back_btn.clicked.connect(_later(self._go, -1))
        self.next_btn.clicked.connect(self._next)
        bar.addWidget(self.skip_btn); bar.addStretch(1); bar.addWidget(self.back_btn); bar.addWidget(self.next_btn)
        root.addLayout(bar)
        self._show(0)

    def _preview_theme(self, name):
        if self.theme_buttons[name].isChecked():
            update_ui_state(theme=name)
            apply_theme(QApplication.instance())

    def _preview_side(self, key):
        if self.side_buttons[key].isChecked():
            save_settings({"actions_side": key})
            refresh_inspectors()

    def _show(self, i):
        self.stack.setCurrentIndex(i)
        self.step.setText(f"Step {i + 1} of {len(self.PAGES)}")
        self.back_btn.setVisible(i > 0)
        last = i == len(self.PAGES) - 1
        self.next_btn.setText("Start using Acro-Data" if last else "Next")
        self.skip_btn.setVisible(not last)

    def _go(self, d):
        self._show(max(0, min(len(self.PAGES) - 1, self.stack.currentIndex() + d)))

    def _next(self):
        i = self.stack.currentIndex()
        if i == 2:                                           # leaving the PDF page: do what was asked
            self.result_note = ""
            if self.pdf_box.isChecked() and self.can_register:
                ok, msg = self._register()
                if ok:
                    self.result_note = "Acro-Data is registered. Choose it for PDF on the Windows page that opens next."
                    self._open_page()
                else:
                    self.result_note = msg
            self.ready_text.setText(self.result_note or "Your choices are saved. You can change any of them in Settings.")
        if i == len(self.PAGES) - 1:
            self._done = True
            mark_first_run_done()
            self.accept()
        else:
            self._go(1)

    def reject(self):
        if not self._done:                                   # skipped or closed: put the look back, but do not nag again
            update_ui_state(theme=self._orig_theme)
            save_settings({"actions_side": self._orig_side})
            apply_theme(QApplication.instance())
            refresh_inspectors()
            mark_first_run_done()
        super().reject()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Acro-Data")
        # Minimum is what the tallest tab (Bulk Renamer) needs before its
        # preview table collapses; the default opens larger than that.
        # The Bulk Renamer is now a split pane and needs real width: below
        # ~1180 the preview's name columns truncate typical filenames.
        # 1180x700 still fits a 1366x768 laptop after the taskbar.
        # MEASURED 2026-08-13 across all seven tabs (eight since 1.4.0): the widest minimum is
        # 776 (Bulk Renamer) and the tallest is 563 (Bulk File Mover) — the
        # PDF Optimizer used to be 686 tall and was what forced this window
        # to be tall at all; its two-column layout brought it to 415.
        # Width stays at 1180 for readability, not fit: below it the
        # Renamer's preview name columns truncate typical filenames.
        # Height lowered 700 -> 640, verified with no truncation and no
        # clipped text on every tab in all three themes down to 600.
        # Floor is small enough to snap two windows side by side or fit a
        # scaled laptop screen; below a panel's own minimum its tab scrolls
        # (LazyTab). 1360x860 stays the default on a screen that can hold it.
        self.setMinimumSize(720, 480)
        size = (1360, 860)
        try:
            avail = QApplication.primaryScreen().availableGeometry()
            size = (min(size[0], max(720, avail.width() - 40)),
                    min(size[1], max(480, avail.height() - 60)))
        except Exception:
            pass
        self.resize(*size)

        tabs = QTabWidget()
        tabs.setTabBar(AdaptiveTabBar(tabs))
        # Every tab is lazy EXCEPT the first, which is about to be shown
        # anyway. See LazyTab for the measurement this is based on.
        for factory, title in (
                (MergerPanel, "PDF Merger"),
                (SplitterPanel, "PDF Splitter"),
                (OptimizerPanel, "PDF Optimizer"),
                (OrganizerPanel, "Page Organizer"),
                (RenamerPanel, "Bulk Renamer"),
                (MoverPanel, "Bulk File Mover"),
                (lambda: SettingsPanel(on_theme_change=self._retheme, on_layout_change=self.apply_layout), "Settings"),
                (HelpPanel, "Help")):
            tabs.addTab(LazyTab(factory), title)
        tabs.setIconSize(QSize(18, 18))

        self.tabs = tabs
        self._decorate_tabs()
        self.shell = TrayShell()
        self.shell.attach_tabs(tabs)
        self.setCentralWidget(self.shell)
        self._layout = ""
        self.apply_layout()

        state = load_ui_state()
        geo = state.get("window_geometry")
        if isinstance(geo, str) and geo:
            try:
                # restoreGeometry clamps to the available screens, so a
                # window last seen on a monitor that is now unplugged still
                # comes back on screen.
                self.restoreGeometry(QByteArray.fromHex(geo.encode("ascii")))
            except Exception:
                pass
        # 1.4.0 put a tab at position 3; saved positions from before that
        # point one tab too early for everything from the Renamer onward.
        tl = state.get("tab_layout", 1)
        if isinstance(tl, bool) or not isinstance(tl, int) or tl < 2:
            old_default = setting("default_tab")
            if isinstance(old_default, int) and old_default >= 3:
                save_settings({"default_tab": old_default + 1})
            old_last = state.get("last_tab")
            if isinstance(old_last, int) and not isinstance(old_last, bool) and old_last >= 3:
                state["last_tab"] = old_last + 1
            if isinstance(state.get("last_tab"), int) and not isinstance(state.get("last_tab"), bool):
                update_ui_state(tab_layout=2, last_tab=state["last_tab"])
            else:
                update_ui_state(tab_layout=2)
        default_tab = setting("default_tab")
        last_tab = default_tab if default_tab >= 0 else state.get("last_tab")
        if isinstance(last_tab, int) and 0 <= last_tab < tabs.count():
            tabs.setCurrentIndex(last_tab)

        self._install_shortcuts()

    # ------------------------------------------------------------ layout
    def apply_layout(self, mode=None):
        """Show the Tray layout or the classic tab strip. The same tab widget (and so every open panel and
        its state) stays where it is; the shell hides or shows the tray and tool buttons around it."""
        if mode not in ("tray", "classic"):
            mode = setting("ui_layout")
        if mode == self._layout:
            return
        self.shell.set_mode(mode)
        self._layout = mode

    def add_to_tray(self, paths):
        """Put files in the tray from anywhere (a tool's Send To menu) and show it. Returns the one-line summary."""
        text = self.shell.tray.add_paths(list(paths))
        if self._layout == "tray":
            self.shell._set_tray_wanted(True)
        return text

    def open_from_tray(self, path: str, kind: str, as_b: bool = False):
        """Double-click on a tray row: show the PDF (or the folder) in the Page Organizer. With as_b the PDF is shown
        in the Compare pane (B) beside whatever the Organizer has open."""
        title = "Page Organizer"
        holder = None
        for i in range(self.tabs.count()):
            if self.tabs.tabBar().title(i) == title:
                holder = self.tabs.widget(i)
                break
        if holder is None:
            return False, "The Page Organizer is not available."
        panel = holder.ensure() if isinstance(holder, LazyTab) else holder
        if getattr(panel, "is_busy", lambda: False)():
            return False, "The Page Organizer is busy. Wait for it to finish, then double-click again."
        if kind == "folder":
            ok = bool(panel.receive_folder(path))
            what = "Opened the folder in the Page Organizer."
        elif as_b:
            ok = bool(panel.open_b(path))
            what = "Showing it in B, the compare pane of the Page Organizer."
        else:
            ok = bool(panel.open_pdf_in_reader(path))
            what = "Opened in the Page Organizer."
        if ok:
            for i in range(self.tabs.count()):
                if self.tabs.tabBar().title(i) == title:
                    self.tabs.setCurrentIndex(i)
        return ok, (what if ok else "The Page Organizer could not open that. Its own status line says why.")

    def feed_from_tray(self, verb: str):
        """Hand the ticked tray rows to the tool for `verb`. Returns (ok, message)."""
        plan = plan_tray_feed(verb, self.shell.tray.ticked())
        if not plan["ok"]:
            return False, plan["problem"]
        title = next(t for k, _l, _s, _i, t in TRAY_VERBS if k == verb)
        holder = None
        for i in range(self.tabs.count()):
            if self.tabs.tabBar().title(i) == title:
                holder = self.tabs.widget(i)
                self.tabs.setCurrentIndex(i)
                break
        if holder is None:
            return False, "That tool is not available."
        panel = holder.ensure() if isinstance(holder, LazyTab) else holder
        if getattr(panel, "is_busy", lambda: False)():
            return False, "That tool is busy. Wait for it to finish, then try again."
        if verb == "merge":
            ok = bool(panel.receive_pair(*plan["pair"]))
        elif verb == "pages" and plan["folder"]:
            ok = bool(panel.receive_folder(plan["folder"]))
        elif verb in ("rename", "move"):
            ok = bool(panel.receive_folder(plan["folder"]))
        else:
            ok = bool(panel.receive_paths(list(plan["paths"])))
        if ok:
            after = {
                "merge": "It is asking you to confirm before anything is written, and a backup is kept.",
                "split": "It is asking you to confirm before anything is split; originals are left untouched.",
                "shrink": "It has started; your originals are kept and the smaller copies go in its output folder.",
                "pages": "Nothing has been changed; save from the Page Organizer when you are done.",
                "rename": "Nothing has been renamed yet; check the preview, then run it with the tool's own button.",
                "move": "Nothing has been moved yet; check the preview, then run it with the tool's own button.",
            }[verb]
            return True, f"Sent to {title}. {after}"
        return False, "The tool did not take them; its own status line says why."

    # ------------------------------------------------------------ keyboard
    def _install_shortcuts(self):
        def act(seq, slot):
            a = QAction(self)
            a.setShortcut(QKeySequence(seq))
            a.setShortcutContext(Qt.WindowShortcut)
            a.triggered.connect(slot)
            self.addAction(a)
            return a

        for i in range(min(self.tabs.count(), 9)):
            act(f"Ctrl+{i + 1}", lambda _c=False, idx=i: self.tabs.setCurrentIndex(idx))
        act("F1", lambda: self.tabs.setCurrentIndex(self.tabs.count() - 1))
        act("Ctrl+O", lambda: self._panel_hook("SHORTCUT_OPEN"))
        act("Ctrl+Return", lambda: self._panel_hook("SHORTCUT_RUN"))
        act("Ctrl+Enter", lambda: self._panel_hook("SHORTCUT_RUN"))
        # Ctrl+Z and Ctrl+Shift+Z both undo the tab's last change. A text box
        # that has focus claims Ctrl+Z for itself (Qt asks the focused widget
        # first), so there Ctrl+Z still undoes typing; Ctrl+Shift+Z always
        # means the tab's undo.
        act("Ctrl+S", lambda: self._panel_hook("SHORTCUT_SAVE"))
        act("Ctrl+Z", lambda: self._panel_hook("SHORTCUT_UNDO"))
        act("Ctrl+Shift+Z", lambda: self._panel_hook("SHORTCUT_UNDO"))
        act("F5", lambda: self._panel_hook("SHORTCUT_REFRESH"))
        act("Esc", self._escape)
        act("Ctrl+T", self.shell.toggle_tray)
        act("Ctrl+J", self.toggle_actions)

    def toggle_actions(self):
        """Ctrl+J: collapse or bring back the Actions column of every tool."""
        inspector_toggle()

    def _panel_hook(self, hook):
        panel = self.panel_at(self.tabs.currentIndex())
        name = getattr(panel, hook, None) if panel is not None else None
        fn = getattr(panel, name, None) if name else None
        if callable(fn) and not (getattr(panel, "is_busy", lambda: False)()):
            fn()

    def _escape(self):
        panel = self.panel_at(self.tabs.currentIndex())
        hook = getattr(panel, "_escape_pressed", None)
        if callable(hook) and hook():
            return
        if panel is not None and getattr(panel, "is_busy", lambda: False)():
            cancel = getattr(panel, "_cancel", None)
            if callable(cancel):
                cancel()

    def _save_window_state(self):
        update_ui_state(
            window_geometry=bytes(self.saveGeometry().toHex()).decode("ascii"),
            last_tab=self.tabs.currentIndex())

    def send_paths(self, target: str, paths) -> bool:
        """Switch to another tab and give it files. False (nothing moved) when that
        tab is busy or cannot take them; the receiving tab says why in its own status."""
        cls = {"merger": MergerPanel, "splitter": SplitterPanel, "optimizer": OptimizerPanel,
               "organizer": OrganizerPanel, "renamer": RenamerPanel}.get(target)
        if cls is None:
            return False
        for i in range(self.tabs.count()):
            holder = self.tabs.widget(i)
            if getattr(holder, "_factory", None) is cls or isinstance(holder, cls):
                panel = holder.ensure() if isinstance(holder, LazyTab) else holder
                if getattr(panel, "is_busy", lambda: False)():
                    return False
                fn = getattr(panel, "receive_paths", None)
                if not callable(fn):
                    return False
                self.tabs.setCurrentIndex(i)
                return bool(fn(list(paths)))
        return False

    def run_first_run_guide(self):
        """The welcome / setup guide. Safe to run again from Settings."""
        dlg = FirstRunDialog(self)
        dlg.exec()
        return dlg

    def open_pdf_from_launch(self, path: str) -> bool:
        """A PDF handed to the app by Windows (double-click / Open with) or by a
        second launch: show it in the Page Organizer's reader."""
        for i in range(self.tabs.count()):
            holder = self.tabs.widget(i)
            if getattr(holder, "_factory", None) is OrganizerPanel or isinstance(holder, OrganizerPanel):
                self.tabs.setCurrentIndex(i)
                panel = holder.ensure() if isinstance(holder, LazyTab) else holder
                break
        else:
            return False
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        if setting("launch_reader_full"):
            # A PDF from Explorer is for reading: fill the screen and give the page the room. The file tray
            # steps aside for this session only (the saved choice is left alone); the Actions column stays.
            if not (self.isMaximized() or self.isFullScreen()):
                self.showMaximized()
            self.shell.hide_tray_for_session()
        self.show(); self.raise_(); self.activateWindow()
        return bool(panel.open_pdf_in_reader(path))

    def panel_at(self, index):
        """The real panel for a tab, or None if it has never been opened.

        A tab that was never opened cannot be busy and has nothing to repaint,
        so None is a legitimate answer and callers treat it as 'nothing to do'
        rather than building the panel just to ask it a question.
        """
        holder = self.tabs.widget(index)
        return holder.panel if isinstance(holder, LazyTab) else holder

    def panels(self):
        for i in range(self.tabs.count()):
            p = self.panel_at(i)
            if p is not None:
                yield i, p

    def closeEvent(self, event):
        """
        Closing the app on a live QThread is a crash, and a half-written
        optimized copy is worse than none.

        Verified before this was added: MainWindow had no closeEvent at all,
        so quitting during a run destroyed a running worker mid-file.
        """
        # EVERY tab that can be mid-operation, not just the Optimizer. The
        # Renamer's two-phase rename is the worst one to interrupt: files sit
        # briefly under temporary names the file list deliberately hides.
        # Unsaved page edits, then any background reader (page images, page
        # counts, search): a live QThread destroyed with its window aborts.
        for i, panel in self.panels():
            if hasattr(panel, "has_unsaved") and panel.has_unsaved():
                ans = Msg.question(
                    self, "Unsaved Page Changes",
                    f"{self.tabs.tabBar().title(i)} has page changes that are not "
                    "saved. Close and lose them?\n\nThe PDF itself is unchanged.",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if ans != QMessageBox.Yes:
                    event.ignore()
                    return
        for i, panel in self.panels():
            waiting = panel.launch_waiting() if hasattr(panel, "launch_waiting") else 0
            if waiting:
                ans = Msg.question(
                    self, "PDFs Still Waiting",
                    f"{plural(waiting, 'PDF')} you opened from Explorer {'has' if waiting == 1 else 'have'} not been "
                    "looked at yet. The list is not kept after closing.\n\nClose anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if ans != QMessageBox.Yes:
                    event.ignore()
                    return
        for i, panel in self.panels():
            if hasattr(panel, "stop_background") and not panel.stop_background():
                Msg.information(self, "Still Working",
                                "A background read has not finished. Wait a moment "
                                "and close again.")
                event.ignore()
                return
        busy = []
        for i, panel in self.panels():
            if getattr(panel, "_busy", False) or (
                    hasattr(panel, "is_busy") and panel.is_busy()):
                busy.append((self.tabs.tabBar().title(i), panel))
        if busy:
            names = ", ".join(name for name, _p in busy)
            answer = Msg.question(
                self, "Still Working",
                f"{names} is still working.\n\n"
                "Close anyway? Each tab stops after the file it is on (the "
                "Bulk Renamer finishes its run first, so no file is left under "
                "a temporary name). Your originals are not at risk either way.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            still_running = []
            for name, panel in busy:
                if hasattr(panel, "stop_for_shutdown"):
                    if not panel.stop_for_shutdown():
                        still_running.append(name)
            if still_running:
                # Destroying a live QThread aborts the process; keep the
                # window until the file in flight has finished.
                Msg.information(
                    self, "Still Working",
                    ", ".join(still_running) + " has not finished the file it "
                    "is on. Wait a moment and close again.")
                event.ignore()
                return
        if not self.shell.tray.stop_for_shutdown():
            Msg.information(self, "Still Working",
                            "The tray is still reading a file. Wait a moment and close again.")
            event.ignore()
            return
        self._save_window_state()
        try:
            clear_recent_paths()
        except Exception:
            pass                                   # never keep the app open over this
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        apply_titlebar_theme(self, _CURRENT)
        self.sync_actions_room()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_actions_room()

    def sync_actions_room(self):
        """Fold the Actions columns away when the tools have too little width (small window, or the tray open)."""
        try:
            set_actions_room(self.tabs.width())
        except RuntimeError:
            pass

    def _decorate_tabs(self):
        for i in range(self.tabs.count()):
            key = _TAB_ICONS.get(self.tabs.tabBar().title(i))
            if key:
                self.tabs.setTabIcon(i, tab_icon(key))

    def _retheme(self):
        """The preview table sets two colours from Python, so it has to be
        repainted after a theme switch — the stylesheet alone cannot reach
        an already-constructed QTableWidgetItem's foreground."""
        apply_titlebar_theme(self, _CURRENT)
        self._decorate_tabs()
        self.shell.restyle()
        for _i, _panel in self.panels():
            decorate_icons(_panel)
        # Only panels that exist. An unopened tab builds AFTER the theme change
        # and is styled correctly on the way up.
        for _i, panel in self.panels():
            if isinstance(panel, RenamerPanel):
                panel.refresh_preview()
            elif isinstance(panel, OptimizerPanel):
                panel.refresh_results()


def shared_folder() -> str:
    """The folder holding the shared profile file, if one is configured. Also
    where a version marker is looked for."""
    cpath = shared_profiles_path()
    return os.path.dirname(cpath) if cpath else ""


def _version_tuple(text: str):
    parts = []
    for chunk in str(text).strip().split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or [0])


def available_version() -> str:
    """The version named in version.txt on the shared folder, or "".

    Deliberately a plain text file: IT drops the new build in the share and
    edits one line. No updater service, no elevation, nothing to install.
    """
    folder = shared_folder()
    if not folder:
        return ""
    try:
        with open(os.path.join(folder, "version.txt"), "r", encoding="utf-8") as fh:
            return fh.read().strip().splitlines()[0].strip()
    except Exception:
        return ""


def update_note() -> str:
    """A sentence to show the user, or "". Never blocks anything."""
    latest = available_version()
    if not latest:
        return ""
    try:
        if _version_tuple(latest) > _version_tuple(APP_VERSION):
            return (f"Version {latest} is available on the shared folder - "
                    f"this machine is running {APP_VERSION}. Ask IT to update it.")
    except Exception:
        pass
    return ""


def diagnostics_text() -> str:
    """Everything support would otherwise ask for over the phone, in one paste.

    Names paths rather than contents: no filenames, no profile data. What it
    answers is "which build, which settings, did it crash" - the questions that
    currently take ten minutes of walking somebody through %LOCALAPPDATA%.
    """
    lines = [
        f"{APP_NAME} {APP_VERSION}",
        f"when      : {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"user      : {_audit_user()}",
        f"python    : {sys.version.split()[0]}",
        f"platform  : {sys.platform}",
        f"frozen    : {bool(getattr(sys, 'frozen', False))}",
        f"settings  : {_log_dir()}",
    ]
    try:
        profiles, notes = load_profiles()
        shared_set = [p.name for p in profiles if p.source == "shared"]
        local = [p.name for p in profiles if p.source != "shared"]
        lines += [
            f"shared file: {shared_profiles_path() or '(none configured)'}",
            f"shared ver : {available_version() or '(none)'}",
            f"profiles   : {len(shared_set)} shared, {len(local)} local",
            f"  shared   : {', '.join(shared_set) or '-'}",
            f"  local    : {', '.join(local) or '-'}",
        ]
        for n in notes:
            lines.append(f"  note     : {n}")
    except Exception as e:
        lines.append(f"profiles   : could not be read ({e})")

    for label, path in (("undo log ", undo_log_path()),
                        ("audit log", audit_log_path()),
                        ("crash log", crash_log_path())):
        try:
            if os.path.exists(path):
                size = os.path.getsize(path)
                when = time.strftime("%Y-%m-%d %H:%M",
                                     time.localtime(os.path.getmtime(path)))
                lines.append(f"{label}  : {size} bytes, last written {when}")
            else:
                lines.append(f"{label}  : none")
        except OSError:
            lines.append(f"{label}  : unreadable")
    try:
        if os.path.exists(crash_log_path()):
            with open(crash_log_path(), "r", encoding="utf-8", errors="replace") as fh:
                tail = fh.read()[-1500:]
            lines += ["", "--- end of crash log ---", tail]
    except OSError:
        pass
    return "\n".join(lines)


def crash_log_path() -> str:
    return os.path.join(_log_dir(), "crash.log")


def install_crash_handler():
    """
    A windowed .exe has no console, so an unhandled exception in a slot used
    to end as a vanished or frozen window with nothing for support to act on.
    """
    def handle(exc_type, exc, tb):
        import traceback
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        stamp = f"{APP_NAME} v{APP_VERSION}\n{'-' * 60}\n"
        try:
            with open(crash_log_path(), "a", encoding="utf-8") as fh:
                fh.write(stamp + text + "\n")
        except OSError:
            pass
        try:
            box = QMessageBox()
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("Something Went Wrong")
            box.setText(
                "Acro-Data hit a problem it did not expect.\n\n"
                "Your files have not been changed by this error. The details "
                "have been written to:\n" + crash_log_path() + "\n\n"
                "Please attach that file if you report the problem."
            )
            box.setDetailedText(stamp + text)
            box.exec()
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = handle


# ---------------------------------------------------------------------
# --cli: the same engine without the window.
#
#   AcroD.exe --cli optimize FILE_OR_FOLDER... [--ceiling MB] [--flatten]
#   AcroD.exe --cli split PDF... [--after N]
#   AcroD.exe --cli merge BASE.pdf REF.pdf
#   AcroD.exe --cli diagnostics
#
# The exe is built --windowed, so it has no console: every line below is
# ALSO appended to cli.log in the settings folder, and the exit code is the
# result (0 ok, 1 something failed, 2 bad arguments). From a Python prompt
# it prints normally. A watch-folder mode was considered and NOT built: an
# invisible watcher in a windowed exe has no way to be stopped or seen -
# that needs a tray icon, which is a new surface and its own decision.
# ---------------------------------------------------------------------

def cli_log_path() -> str:
    return os.path.join(_log_dir(), "cli.log")


def run_cli(argv, out=None) -> int:
    """argv is everything AFTER '--cli'. Returns the process exit code."""
    import argparse

    lines = []

    def say(text=""):
        lines.append(text)
        if out is not None:
            try:
                print(text, file=out)
            except Exception:
                pass

    parser = argparse.ArgumentParser(prog="AcroD --cli", add_help=True)
    sub = parser.add_subparsers(dest="cmd")
    p_opt = sub.add_parser("optimize", help="shrink PDFs under a size limit")
    p_opt.add_argument("paths", nargs="+")
    p_opt.add_argument("--ceiling", type=float, default=None, metavar="MB")
    p_opt.add_argument("--flatten", action="store_true",
                       help="flatten pages to images regardless of size")
    p_split = sub.add_parser("split", help="split PDFs after page N")
    p_split.add_argument("paths", nargs="+")
    p_split.add_argument("--after", type=int, default=1, metavar="N")
    p_merge = sub.add_parser("merge", help="append REF onto BASE, overwriting BASE")
    p_merge.add_argument("base")
    p_merge.add_argument("ref")
    sub.add_parser("diagnostics", help="print the support diagnostics block")

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code not in (0, None) else 0
    if not args.cmd:
        parser.print_usage(out)
        return 2

    failures = 0
    say(f"{APP_NAME} {APP_VERSION} --cli {args.cmd}  ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    if args.cmd == "diagnostics":
        say(diagnostics_text())
    elif args.cmd == "merge":
        try:
            merge_onto(args.base, args.ref)
            say(f"merged: {args.ref} -> {args.base}")
        except (PdfOpError, OSError) as exc:
            failures += 1
            say(f"FAILED: {exc}")
    elif args.cmd == "split":
        for path in args.paths:
            try:
                cover, rest = split_pdf(path, args.after)
                say(f"split: {path} -> {cover}, {rest}")
            except (PdfOpError, OSError) as exc:
                failures += 1
                say(f"FAILED: {path}: {exc}")
    elif args.cmd == "optimize":
        pdfs = []
        for path in args.paths:
            if os.path.isdir(path):
                pdfs.extend(collect_files(path, False, "pdf"))
            elif path.lower().endswith(".pdf") and os.path.isfile(path):
                pdfs.append(path)
            else:
                say(f"skipped (not a PDF): {path}")
        ceiling_mb = args.ceiling
        if ceiling_mb is None:
            try:
                ceiling_mb = float(load_ui_state().get("optimizer_ceiling_mb",
                                                       DEFAULT_SIZE_CEILING_MB))
            except (TypeError, ValueError):
                ceiling_mb = DEFAULT_SIZE_CEILING_MB
        options = OptimizeOptions(
            mode="smallest", out_dir_name=setting("optimized_dir_name"),
            ceiling_bytes=int(ceiling_mb * 1_000_000), allow_gray=True,
            allow_flatten=bool(setting("optimizer_allow_flatten")),
            flatten_dpi=int(setting("optimizer_flatten_dpi")),
            force_flatten=args.flatten,
            sanitize=SanitizeOptions())
        for path in pdfs:
            try:
                res = optimize_pdf(path, options)
            except (PdfOpError, OSError) as exc:
                failures += 1
                say(f"FAILED: {path}: {exc}")
                continue
            if res.ok:
                say(f"optimized: {path} -> {res.dst}  "
                    f"{res.src_bytes / 1e6:.2f} MB -> {res.dst_bytes / 1e6:.2f} MB  [{res.tier}]"
                    + ("" if res.under_ceiling or not res.ceiling_applied else "  STILL OVER LIMIT"))
            else:
                failures += 1
                say(f"FAILED: {path}: {res.error or 'skipped'}")
        if not pdfs:
            say("nothing to optimize")
    say(f"done: {failures} failed")
    try:
        with open(cli_log_path(), "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n\n")
    except OSError:
        pass
    return 1 if failures else 0


def install_main_thread_gc(app, interval_ms=2000):
    """Run the cyclic garbage collector on the GUI thread only.

    Automatic collection fires in whichever thread allocates next. With worker
    threads running (page rendering, folder rescans) that can be a worker, and
    collecting a closed window there destroys Qt widgets off the GUI thread - an
    intermittent native crash. Reference counting still frees almost everything
    at once; this only moves the cycle sweep to a timer on the GUI thread.
    Returns the timer (the caller keeps it alive)."""
    import gc
    gc.disable()
    t = QTimer(app)
    t.setInterval(int(interval_ms))
    t.timeout.connect(gc.collect)
    t.start()
    return t


def launch_pdf_args(argv):
    """Every existing PDF Windows passed on the command line (double-click / Open with),
    in order. Anything else is ignored."""
    return [os.path.abspath(a) for a in argv[1:] if a.lower().endswith(".pdf") and os.path.isfile(a)]


def post_open_request(path: str) -> bool:
    """Hand a PDF to the copy of the app that is already running. One small file
    per request (atomic rename), so two quick double-clicks both arrive."""
    try:
        d = _log_dir()
        name = f"open-request-{time.time_ns():020d}-{os.getpid()}"      # sorts by arrival, not by process id
        tmp = os.path.join(d, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(path)
        os.replace(tmp, os.path.join(d, name + ".txt"))
        return True
    except OSError:
        return False


def take_open_requests():
    """Oldest first; each request file is consumed."""
    out = []
    try:
        d = _log_dir()
        for n in sorted(os.listdir(d)):
            if n.startswith("open-request-") and n.endswith(".txt"):
                f = os.path.join(d, n)
                try:
                    if time.time() - os.path.getmtime(f) > 600:
                        continue                      # left by a launch nobody answered
                    with open(f, encoding="utf-8") as fh:
                        out.append(fh.read().strip())
                finally:
                    try:
                        os.remove(f)
                    except OSError:
                        pass
    except OSError:
        pass
    return [p for p in out if p]


def start_open_watcher(window, interval_ms=400):
    """Poll for PDFs sent by later launches. Plain files, no sockets: the build
    leaves the networking module out on purpose."""
    t = QTimer(window)
    t.setInterval(interval_ms)
    def _poll():
        for path in take_open_requests():
            try:
                window.open_pdf_from_launch(path)
            except Exception:            # one bad request must not lose the others already taken
                import traceback
                traceback.print_exc()
    t.timeout.connect(_poll)
    t.start()
    return t


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        Splash().close()                       # a command-line run has no window to hand the splash over to
        # No window, no single-instance lock (a CLI run beside the open app
        # is legitimate; both write only under the atomic-write discipline).
        sys.exit(run_cli(sys.argv[2:], out=sys.stdout))

    app = QApplication(sys.argv)
    install_crash_handler()
    _gc_timer = install_main_thread_gc(app)
    try:
        app.setWindowIcon(app_icon())
    except Exception:
        pass       # a missing icon must never stop the app starting

    # One instance. Two copies share ui_state.json, the preset file and the
    # undo log, and every writer does read-modify-write - so a second copy
    # silently reverts the first's settings and can overwrite the undo record
    # the first one still needs.
    _lock = QLockFile(os.path.join(_log_dir(), "acrod.lock"))
    _lock.setStaleLockTime(0)
    _launch_pdfs = launch_pdf_args(sys.argv)
    if not _lock.tryLock(100):
        log_startup("another copy is already running: this start was handed over and closed")
        if _launch_pdfs and all([post_open_request(_p) for _p in _launch_pdfs]):
            sys.exit(0)        # the running window picks them up
        Msg.information(
            None, APP_NAME,
            "Acro-Data is already running.\n\n"
            "Use the window that is already open — running two copies can "
            "undo each other's settings and the Bulk Renamer's undo record."
        )
        sys.exit(0)
    # Fusion is set explicitly rather than left to the platform default.
    # MEASURED: native-drawn indicators follow QPalette under Fusion but the
    # Windows native style ignores it, which would ship Dark mode with light
    # checkboxes. Fusion makes Windows behave like what the suite can test.
    app.setStyle("Fusion")
    apply_theme(app)

    # The theme is applied first so the splash is painted in the user's own
    # colours rather than flashing a light box at someone running Dark.
    _forced = "--first-run" in sys.argv
    _first_run = (first_run_pending() or _forced) and not _launch_pdfs   # a PDF double-click goes straight to the reader; the guide waits
    log_startup(f"first_run_pending={first_run_pending()} forced={_forced} pdf_launch={bool(_launch_pdfs)} -> guide={'yes' if _first_run else 'no'}")
    splash = Splash(app)
    splash.text("Loading settings...")
    window = MainWindow()
    splash.text("Ready")
    window.show()
    splash.close(window)
    _open_timer = start_open_watcher(window)
    if _first_run:
        mark_first_run_done()                                  # written first: a crash in the guide must not bring it back every start
        QTimer.singleShot(350, _later(window.run_first_run_guide))
    if _launch_pdfs:
        def _open_launched():
            for _p in _launch_pdfs:
                window.open_pdf_from_launch(_p)
        QTimer.singleShot(0, _open_launched)
    sys.exit(app.exec())
