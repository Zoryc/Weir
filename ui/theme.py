"""Light and dark themes: palette + stylesheet, following the system color scheme.

Fusion is used on every platform so the app looks the same on Windows and Linux;
colours, rounded corners and spacing come from the stylesheet built here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from core.paths import IS_WINDOWS

from .icon import write_glyph_png


@dataclass(frozen=True)
class Theme:
    dark: bool
    window: str
    card: str
    alt: str
    hover: str
    border: str
    border_strong: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_disabled: str
    accent_soft: str
    selection: str
    track: str
    success: str
    warning: str
    danger: str
    scroll: str

    def color(self, name: str) -> QColor:
        return QColor(getattr(self, name))


LIGHT = Theme(
    dark=False,
    window="#f3f4f7",
    card="#ffffff",
    alt="#f7f8fa",
    hover="#eef1f6",
    border="#e1e4ea",
    border_strong="#cdd2da",
    text="#1d2129",
    muted="#6b7280",
    accent="#2f6fed",
    accent_hover="#2763dc",
    accent_pressed="#1f53bd",
    accent_disabled="#a9c0f5",
    accent_soft="#e8effe",
    selection="#dde8fd",
    track="#e6e9ef",
    success="#1e9a55",
    warning="#c47a0a",
    danger="#d93025",
    scroll="#c3c8d1",
)

DARK = Theme(
    dark=True,
    window="#1a1c20",
    card="#23262b",
    alt="#292c32",
    hover="#2d3138",
    border="#33373e",
    border_strong="#454a53",
    text="#e8eaed",
    muted="#9aa0aa",
    accent="#5b8cff",
    accent_hover="#6f9bff",
    accent_pressed="#4a79e8",
    accent_disabled="#344466",
    accent_soft="#26324a",
    selection="#2c3a57",
    track="#363a42",
    success="#4cc27e",
    warning="#f0b450",
    danger="#ff6b5f",
    scroll="#4a4f58",
)

_current: Theme = LIGHT


def current() -> Theme:
    return _current


def build_palette(t: Theme) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.window,
        QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.card,
        QPalette.ColorRole.AlternateBase: t.alt,
        QPalette.ColorRole.ToolTipBase: t.card,
        QPalette.ColorRole.ToolTipText: t.text,
        QPalette.ColorRole.PlaceholderText: t.muted,
        QPalette.ColorRole.Text: t.text,
        QPalette.ColorRole.Button: t.card,
        QPalette.ColorRole.ButtonText: t.text,
        QPalette.ColorRole.BrightText: t.danger,
        QPalette.ColorRole.Link: t.accent,
        QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.Light: t.card,
        QPalette.ColorRole.Midlight: t.hover,
        QPalette.ColorRole.Mid: t.border_strong,
        QPalette.ColorRole.Dark: t.border_strong,
        QPalette.ColorRole.Shadow: "#000000",
    }
    for role, value in roles.items():
        p.setColor(role, QColor(value))
    if hasattr(QPalette.ColorRole, "Accent"):
        p.setColor(QPalette.ColorRole.Accent, QColor(t.accent))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.muted))
    return p


def build_stylesheet(t: Theme, chevron: str, chevron_accent: str, check: str) -> str:
    return f"""
QMainWindow, QDialog {{ background: {t.window}; }}
QToolTip {{ background: {t.card}; color: {t.text}; border: 1px solid {t.border}; padding: 6px 8px; }}

QFrame#card {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 12px; }}
QFrame#card QLabel {{ background: transparent; }}
QFrame#divider {{ background: {t.border}; border: none; max-height: 1px; min-height: 1px; }}
QLabel#appTitle {{ font-size: 15pt; font-weight: 600; }}
QLabel#cardTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#muted, QLabel#fieldLabel {{ color: {t.muted}; }}
QLabel#fieldLabel {{ font-weight: 600; }}

QPushButton {{
    background: {t.card}; color: {t.text};
    border: 1px solid {t.border_strong}; border-radius: 7px;
    padding: 6px 14px; min-height: 18px;
}}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.alt}; border-color: {t.border}; }}
QPushButton:focus {{ border-color: {t.accent}; }}
QPushButton#primary {{
    background: {t.accent}; color: #ffffff; border: none; border-radius: 9px;
    padding: 10px 26px; font-weight: 600; font-size: 11pt;
}}
QPushButton#primary:hover {{ background: {t.accent_hover}; }}
QPushButton#primary:pressed {{ background: {t.accent_pressed}; }}
QPushButton#primary:disabled {{ background: {t.accent_disabled}; color: rgba(255, 255, 255, 190); }}
QPushButton#secondary {{ padding: 10px 20px; border-radius: 9px; font-size: 11pt; }}
QPushButton#accentSmall {{ background: {t.accent}; color: #ffffff; border: none; font-weight: 600; }}
QPushButton#accentSmall:hover {{ background: {t.accent_hover}; }}
QPushButton#accentSmall:pressed {{ background: {t.accent_pressed}; }}
QPushButton#flat {{ background: transparent; border: none; color: {t.muted}; padding: 4px 8px; }}
QPushButton#flat:hover {{ color: {t.accent}; background: {t.accent_soft}; }}
QPushButton#flat:disabled {{ color: {t.border_strong}; background: transparent; }}

QToolButton#sectionToggle {{
    background: transparent; border: none; color: {t.muted};
    font-weight: 600; padding: 4px 6px 4px 2px;
}}
QToolButton#sectionToggle:hover {{ color: {t.accent}; }}
QToolButton#menuButton {{
    background: {t.card}; color: {t.text}; border: 1px solid {t.border_strong};
    border-radius: 7px; padding: 6px 28px 6px 12px;
}}
QToolButton#menuButton:hover {{ background: {t.hover}; }}
QToolButton#menuButton::menu-indicator {{
    image: url("{chevron}"); subcontrol-origin: padding; subcontrol-position: center right;
    right: 9px; width: 10px; height: 10px;
}}
QToolButton#closeButton {{ background: transparent; border: none; color: {t.muted}; padding: 2px 6px; }}
QToolButton#closeButton:hover {{ color: {t.text}; }}

QLineEdit, QComboBox {{
    background: {t.card}; color: {t.text};
    border: 1px solid {t.border_strong}; border-radius: 7px;
    padding: 6px 9px; min-height: 18px;
    selection-background-color: {t.accent}; selection-color: #ffffff;
}}
QLineEdit:hover, QComboBox:hover {{ border-color: {t.muted}; }}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border-color: {t.accent}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {t.muted}; background: {t.alt}; }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 28px; border: none; background: transparent;
}}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 11px; height: 11px; }}
QComboBox::down-arrow:on {{ image: url("{chevron_accent}"); }}
QComboBox QAbstractItemView {{
    background: {t.card}; color: {t.text}; border: 1px solid {t.border};
    selection-background-color: {t.selection}; selection-color: {t.text};
    outline: 0; padding: 4px;
}}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 8px; }}

QPlainTextEdit {{
    background: {t.card}; color: {t.text};
    border: 1px solid {t.border_strong}; border-radius: 8px; padding: 4px;
    selection-background-color: {t.accent}; selection-color: #ffffff;
}}
QPlainTextEdit:focus {{ border-color: {t.accent}; }}
QPlainTextEdit#links {{ padding: 6px 4px; font-size: 10.5pt; }}
QPlainTextEdit#links[dragActive="true"] {{ border: 2px dashed {t.accent}; background: {t.accent_soft}; }}
QPlainTextEdit#log {{ background: {t.alt}; border-color: {t.border}; }}

QCheckBox {{ spacing: 8px; color: {t.text}; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid {t.border_strong}; background: {t.card};
}}
QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url("{check}"); }}
QCheckBox::indicator:checked:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QCheckBox::indicator:disabled {{ background: {t.alt}; border-color: {t.border}; }}

QScrollArea#page {{ background: {t.window}; border: none; }}
QWidget#pageContent {{ background: {t.window}; }}

QTreeWidget#queue {{
    background: {t.card}; border: none; outline: 0;
    selection-background-color: {t.selection}; selection-color: {t.text};
}}
QTreeWidget#queue::item {{ border-bottom: 1px solid {t.border}; padding: 0 4px; }}
QTreeWidget#queue::item:hover {{ background: {t.alt}; }}
QTreeWidget#queue::item:selected {{ background: {t.selection}; color: {t.text}; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
    background: {t.card}; color: {t.muted}; border: none;
    border-bottom: 1px solid {t.border}; padding: 6px 8px; font-weight: 600;
}}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.scroll}; border-radius: 3px; min-height: 32px; margin: 0 2px; }}
QScrollBar::handle:horizontal {{ background: {t.scroll}; border-radius: 3px; min-width: 32px; margin: 2px 0; }}
QScrollBar::handle:hover {{ background: {t.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{ background: {t.card}; color: {t.text}; border: 1px solid {t.border}; padding: 5px; }}
QMenu::item {{ padding: 7px 26px 7px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {t.selection}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 8px; }}

QStatusBar {{ background: {t.window}; color: {t.muted}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {t.muted}; padding: 0 4px; }}

QProgressBar {{
    background: {t.track}; border: none; border-radius: 4px;
    min-height: 8px; max-height: 8px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 4px; }}

QFrame#errorBanner {{
    background: {_mix(t.card, t.danger, 0.10)}; border: 1px solid {_mix(t.card, t.danger, 0.45)};
    border-radius: 10px;
}}
QFrame#errorBanner QLabel {{ background: transparent; }}
"""


def _mix(base: str, over: str, amount: float) -> str:
    a, b = QColor(base), QColor(over)
    r = round(a.red() + (b.red() - a.red()) * amount)
    g = round(a.green() + (b.green() - a.green()) * amount)
    bl = round(a.blue() + (b.blue() - a.blue()) * amount)
    return QColor(r, g, bl).name()


def tint(color: QColor, alpha: int) -> QColor:
    tinted = QColor(color)
    tinted.setAlpha(alpha)
    return tinted


class ThemeManager(QObject):
    """Applies the theme matching the system light/dark setting, and re-applies on change."""

    changed = Signal()

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        app.setStyle("Fusion")
        if IS_WINDOWS:
            # Segoe UI 9 pt (the Qt default) feels cramped; 10 pt reads better.
            font = QFont(app.font())
            if font.pointSizeF() < 10:
                font.setPointSizeF(10)
                app.setFont(font)
        cache = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        self._cache = Path(cache) if cache else Path.home() / ".cache" / "YtdlpDownloader"
        app.styleHints().colorSchemeChanged.connect(self.apply)
        self.apply()

    def _is_dark(self) -> bool:
        return self._app.styleHints().colorScheme() == Qt.ColorScheme.Dark

    def _glyph_file(self, name: str, color: str) -> str:
        """Stylesheets need image files: render the glyph once into the cache folder."""
        path = self._cache / f"{name}-{color.lstrip('#')}.png"
        if not path.exists():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                write_glyph_png(name, QColor(color), path)
            except OSError:
                return ""
        return path.as_posix()

    def apply(self, *_args: object) -> None:
        global _current
        theme = DARK if self._is_dark() else LIGHT
        _current = theme
        self._app.setPalette(build_palette(theme))
        self._app.setStyleSheet(
            build_stylesheet(
                theme,
                chevron=self._glyph_file("chevron", theme.muted),
                chevron_accent=self._glyph_file("chevron", theme.accent),
                check=self._glyph_file("check", "#ffffff"),
            )
        )
        self.changed.emit()
