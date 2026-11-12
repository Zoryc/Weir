"""Design system: colours (Theme), shapes and type (Metrics), and UI variants.

A Variant bundles light + dark colours, metrics, extra stylesheet rules and a layout
function (see ui/variants/). ThemeManager applies the active variant — Fusion style,
palette, app font and stylesheet — and follows the system light/dark setting unless
the variant forces one. Fusion is used everywhere so Windows and Linux look the same.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from .icon import write_glyph_png


@dataclass(frozen=True)
class Theme:
    """Colours for one scheme (light or dark) of a variant. All values are #rrggbb."""

    dark: bool
    window: str  # page background
    card: str  # panels, inputs, list background
    alt: str  # subtle alternate background (hover rows, log)
    hover: str  # button hover
    border: str  # hairlines, card borders
    border_strong: str  # input and button borders
    text: str
    muted: str  # secondary text, labels
    accent: str  # primary buttons, focus, progress
    accent_hover: str
    accent_pressed: str
    accent_disabled: str
    accent_soft: str  # tinted backgrounds (drop target, flat hover)
    selection: str  # selected rows / menu items
    track: str  # empty part of progress bars
    success: str
    warning: str
    danger: str
    scroll: str  # scrollbar handle
    on_accent: str = "#ffffff"  # text/icons drawn on the accent colour

    def color(self, name: str) -> QColor:
        return QColor(getattr(self, name))


@dataclass(frozen=True)
class Metrics:
    """Shape, spacing and typography of a variant. Sizes in px unless noted (pt)."""

    font_families: tuple[str, ...] = ()  # body font fallbacks; () = platform UI font
    font_size: float = 10.0  # body size in pt
    heading_families: tuple[str, ...] = ()  # titles; () = body font
    mono_families: tuple[str, ...] = ()  # log font; () = system fixed-width font
    app_title_size: float = 15.0  # pt
    card_title_size: float = 11.0  # pt
    heading_weight: int = 600
    card_radius: int = 12
    control_radius: int = 7  # inputs, combos, normal buttons
    primary_radius: int = 9  # primary/secondary buttons
    control_padding: tuple[int, int] = (6, 9)  # vertical, horizontal
    button_padding: tuple[int, int] = (6, 14)
    primary_padding: tuple[int, int] = (10, 26)
    primary_font_size: float = 11.0  # pt
    border_width: int = 1
    indicator_size: int = 16  # checkbox
    scrollbar_width: int = 11
    row_extra: int = 22  # download row height = two text lines + this


LayoutBuilder = Callable[[Any, Any], None]  # (MainWindow, content QWidget) -> None
GlyphFile = Callable[[str, str], str]  # (glyph name, "#rrggbb") -> PNG path usable in url("...")


@dataclass(frozen=True)
class Variant:
    """One complete look of the main window."""

    key: str
    name: str
    description: str
    light: Theme
    dark: Theme
    build_layout: LayoutBuilder
    metrics: Metrics = field(default_factory=Metrics)
    extra_qss: Callable[[Theme, Metrics, GlyphFile], str] | None = None  # appended to the base QSS
    after_refresh: Callable[[Any, Theme], None] | None = None  # recolour icons etc. (window, theme)
    scheme: str | None = None  # None = follow the system; "light" or "dark" = always that


_theme: Theme | None = None
_metrics: Metrics = Metrics()
_variant: Variant | None = None


def current() -> Theme:
    """Colours of the active variant and scheme."""
    assert _theme is not None, "ThemeManager has not been created yet"
    return _theme


def metrics() -> Metrics:
    return _metrics


def variant() -> Variant | None:
    return _variant


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
        QPalette.ColorRole.HighlightedText: t.on_accent,
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


def css_families(families: tuple[str, ...]) -> str:
    """'"A", "B"' for a stylesheet font-family value."""
    return ", ".join(f'"{name}"' for name in families)


def mix(base: str, over: str, amount: float) -> str:
    """Blend two #rrggbb colours: amount 0 = base, 1 = over."""
    a, b = QColor(base), QColor(over)
    r = round(a.red() + (b.red() - a.red()) * amount)
    g = round(a.green() + (b.green() - a.green()) * amount)
    bl = round(a.blue() + (b.blue() - a.blue()) * amount)
    return QColor(r, g, bl).name()


def tint(color: QColor, alpha: int) -> QColor:
    tinted = QColor(color)
    tinted.setAlpha(alpha)
    return tinted


def build_stylesheet(t: Theme, m: Metrics, glyphs: dict[str, str]) -> str:
    """Base stylesheet shared by every variant (variants append their own rules)."""
    cv, ch = m.control_padding
    bv, bh = m.button_padding
    pv, ph = m.primary_padding
    bw = m.border_width
    heading_font = f"font-family: {css_families(m.heading_families)};" if m.heading_families else ""
    return f"""
QMainWindow, QDialog {{ background: {t.window}; }}
QToolTip {{ background: {t.card}; color: {t.text}; border: 1px solid {t.border}; padding: 6px 8px; }}
QScrollArea#page {{ background: {t.window}; border: none; }}
QWidget#pageContent {{ background: {t.window}; }}

QFrame#card {{ background: {t.card}; border: {bw}px solid {t.border}; border-radius: {m.card_radius}px; }}
QFrame#card QLabel {{ background: transparent; }}
QFrame#divider {{ background: {t.border}; border: none; max-height: 1px; min-height: 1px; }}
QLabel#appTitle {{ font-size: {m.app_title_size}pt; font-weight: {m.heading_weight}; {heading_font} }}
QLabel#cardTitle {{ font-size: {m.card_title_size}pt; font-weight: {m.heading_weight}; {heading_font} }}
QLabel#muted, QLabel#fieldLabel, QLabel#appTagline {{ color: {t.muted}; }}
QLabel#fieldLabel {{ font-weight: 600; }}

QPushButton {{
    background: {t.card}; color: {t.text};
    border: {bw}px solid {t.border_strong}; border-radius: {m.control_radius}px;
    padding: {bv}px {bh}px; min-height: 18px;
}}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.alt}; border-color: {t.border}; }}
QPushButton:focus {{ border-color: {t.accent}; }}
QPushButton#primary {{
    background: {t.accent}; color: {t.on_accent}; border: none; border-radius: {m.primary_radius}px;
    padding: {pv}px {ph}px; font-weight: 600; font-size: {m.primary_font_size}pt;
}}
QPushButton#primary:hover {{ background: {t.accent_hover}; }}
QPushButton#primary:pressed {{ background: {t.accent_pressed}; }}
QPushButton#primary:disabled {{ background: {t.accent_disabled}; color: {mix(t.accent_disabled, t.on_accent, 0.75)}; }}
QPushButton#secondary {{ padding: {pv}px {max(ph - 6, 10)}px; border-radius: {m.primary_radius}px;
    font-size: {m.primary_font_size}pt; }}
QPushButton#accentSmall {{ background: {t.accent}; color: {t.on_accent}; border: none; font-weight: 600; }}
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
    background: {t.card}; color: {t.text}; border: {bw}px solid {t.border_strong};
    border-radius: {m.control_radius}px; padding: {bv}px 28px {bv}px 12px;
}}
QToolButton#menuButton:hover {{ background: {t.hover}; }}
QToolButton#menuButton::menu-indicator {{
    image: url("{glyphs['chevron']}"); subcontrol-origin: padding; subcontrol-position: center right;
    right: 9px; width: 10px; height: 10px;
}}
QToolButton#closeButton {{ background: transparent; border: none; color: {t.muted}; padding: 2px 6px; }}
QToolButton#closeButton:hover {{ color: {t.text}; }}

QLineEdit, QComboBox {{
    background: {t.card}; color: {t.text};
    border: {bw}px solid {t.border_strong}; border-radius: {m.control_radius}px;
    padding: {cv}px {ch}px; min-height: 18px;
    selection-background-color: {t.accent}; selection-color: {t.on_accent};
}}
QLineEdit:hover, QComboBox:hover {{ border-color: {t.muted}; }}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border-color: {t.accent}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {t.muted}; background: {t.alt}; }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 28px; border: none; background: transparent;
}}
QComboBox::down-arrow {{ image: url("{glyphs['chevron']}"); width: 11px; height: 11px; }}
QComboBox::down-arrow:on {{ image: url("{glyphs['chevron_accent']}"); }}
QComboBox QAbstractItemView {{
    background: {t.card}; color: {t.text}; border: 1px solid {t.border};
    selection-background-color: {t.selection}; selection-color: {t.text};
    outline: 0; padding: 4px;
}}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 8px; }}

QPlainTextEdit {{
    background: {t.card}; color: {t.text};
    border: {bw}px solid {t.border_strong}; border-radius: {m.control_radius + 1}px; padding: 4px;
    selection-background-color: {t.accent}; selection-color: {t.on_accent};
}}
QPlainTextEdit:focus {{ border-color: {t.accent}; }}
QPlainTextEdit#links {{ padding: 6px 4px; font-size: {m.font_size + 0.5}pt; }}
QPlainTextEdit#links[dragActive="true"] {{ border: 2px dashed {t.accent}; background: {t.accent_soft}; }}
QPlainTextEdit#log {{ background: {t.alt}; border-color: {t.border}; }}

QCheckBox {{ spacing: 8px; color: {t.text}; background: transparent; }}
QCheckBox::indicator {{
    width: {m.indicator_size}px; height: {m.indicator_size}px; border-radius: {max(2, m.control_radius - 2)}px;
    border: {bw}px solid {t.border_strong}; background: {t.card};
}}
QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url("{glyphs['check']}"); }}
QCheckBox::indicator:checked:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QCheckBox::indicator:disabled {{ background: {t.alt}; border-color: {t.border}; }}

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

QScrollBar:vertical {{ background: transparent; width: {m.scrollbar_width}px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: {m.scrollbar_width}px; margin: 2px; }}
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
QMenu::indicator {{ width: 14px; height: 14px; left: 6px; }}
QMenu::indicator:checked {{ image: url("{glyphs['check_accent']}"); }}

QStatusBar {{ background: {t.window}; color: {t.muted}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {t.muted}; padding: 0 4px; }}

QProgressBar {{
    background: {t.track}; border: none; border-radius: 4px;
    min-height: 8px; max-height: 8px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 4px; }}

QFrame#errorBanner {{
    background: {mix(t.card, t.danger, 0.10)}; border: 1px solid {mix(t.card, t.danger, 0.45)};
    border-radius: {min(m.card_radius, 10)}px;
}}
QFrame#errorBanner QLabel {{ background: transparent; }}
"""


class ThemeManager(QObject):
    """Applies the active variant and re-applies it when the system light/dark setting changes."""

    changed = Signal()  # colours/stylesheet changed (light/dark switch or new variant)
    variant_changed = Signal(object)  # a different Variant is now active: rebuild the layout

    def __init__(self, app: QApplication, variant: Variant) -> None:
        super().__init__(app)
        self._app = app
        self._variant = variant
        self._forced_scheme: str | None = None
        self._system_font = QFont(app.font())
        app.setStyle("Fusion")
        cache = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        self._cache = Path(cache) if cache else Path.home() / ".cache" / "YtdlpDownloader"
        app.styleHints().colorSchemeChanged.connect(self.apply)
        self.apply()

    @property
    def variant(self) -> Variant:
        return self._variant

    def set_variant(self, new: Variant) -> None:
        if new.key == self._variant.key:
            return
        self._variant = new
        self.apply()
        self.variant_changed.emit(new)

    def force_scheme(self, scheme: str | None) -> None:
        """Override the system light/dark setting ("light", "dark" or None). Used by tools/tests."""
        self._forced_scheme = scheme
        self.apply()

    def is_dark(self) -> bool:
        scheme = self._variant.scheme or self._forced_scheme
        if scheme is not None:
            return scheme == "dark"
        return self._app.styleHints().colorScheme() == Qt.ColorScheme.Dark

    def _glyph_file(self, name: str, color: str) -> str:
        """Stylesheets need image files: render each glyph once into the cache folder."""
        path = self._cache / f"{name}-{color.lstrip('#').lower()}.png"
        if not path.exists():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                write_glyph_png(name, QColor(color), path)
            except OSError:
                return ""
        return path.as_posix()

    def apply(self, *_args: object) -> None:
        global _theme, _metrics, _variant
        v = self._variant
        t = v.dark if self.is_dark() else v.light
        m = v.metrics
        _theme, _metrics, _variant = t, m, v

        font = QFont(self._system_font)
        if m.font_families:
            font.setFamilies(list(m.font_families))
        font.setPointSizeF(m.font_size)
        self._app.setFont(font)
        self._app.setPalette(build_palette(t))
        glyphs = {
            "chevron": self._glyph_file("chevron", t.muted),
            "chevron_accent": self._glyph_file("chevron", t.accent),
            "check": self._glyph_file("check", t.on_accent),
            "check_accent": self._glyph_file("check", t.accent),
        }
        qss = build_stylesheet(t, m, glyphs)
        if v.extra_qss is not None:
            qss += "\n" + v.extra_qss(t, m, self._glyph_file)
        self._app.setStyleSheet(qss)
        self.changed.emit()
