"""Mono: Swiss / minimal-brutalist monochrome.

Black on white (white on black in dark mode) with one signal colour, reserved for
errors and keyboard focus. Everything sits on a strict two-column grid: a narrow
index column of small, letter-spaced monospace captions (01 LINKS, 02 PRESET …) and
a wide field column. Sharp corners, 2px ink borders on every control, a heavy rule
under the masthead, and a solid ink Download slab at the foot of the field column, so
the eye runs straight down it: paste → choose → download.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QResizeEvent
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QSizePolicy, QWidget

from .. import kit
from ..icon import glyph_icon
from ..theme import GlyphFile, Metrics, Theme, Variant, css_families, mix

if TYPE_CHECKING:
    from ..main_window import MainWindow

MONO = ("JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono")
SANS = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", "Arial", "Noto Sans",
        "DejaVu Sans")
DISPLAY = ("Arial", "Helvetica Neue", "Liberation Sans", "Nimbus Sans", "DejaVu Sans")

INDEX_MIN_WIDTH = 128  # the caption column of the grid
GUTTER = 16  # between the caption column and the fields (same as AdvancedPanel's grid)
TABLE_INDENT = 10  # TitleDelegate's text inset

LIGHT = Theme(
    dark=False,
    window="#ffffff",
    card="#ffffff",
    alt="#f4f4f4",
    hover="#ececec",
    border="#dedede",
    border_strong="#0d0d0d",
    text="#0d0d0d",
    muted="#6b6b6b",
    accent="#0d0d0d",  # ink: Download, checked boxes, progress
    accent_hover="#2e2e2e",
    accent_pressed="#4a4a4a",
    accent_disabled="#e6e6e6",
    accent_soft="#f1f1f1",
    selection="#ebebeb",
    track="#e4e4e4",
    success="#16803c",
    warning="#a86400",
    danger="#e63900",  # the signal colour: errors and focus
    scroll="#c6c6c6",
    on_accent="#ffffff",
)

DARK = Theme(
    dark=True,
    window="#0c0c0c",
    card="#0c0c0c",
    alt="#171717",
    hover="#1f1f1f",
    border="#2e2e2e",
    border_strong="#eeeeee",
    text="#eeeeee",
    muted="#9a9a9a",
    accent="#eeeeee",  # inverted: white Download with black text
    accent_hover="#d4d4d4",
    accent_pressed="#b8b8b8",
    accent_disabled="#262626",
    accent_soft="#1b1b1b",
    selection="#262626",
    track="#2b2b2b",
    success="#4fc485",
    warning="#f0b13c",
    danger="#ff5a1f",
    scroll="#3d3d3d",
    on_accent="#0c0c0c",
)


# ------------------------------------------------------------------ decorations

def _caption_font() -> QFont:
    font = QFont()
    font.setFamilies(list(MONO))
    font.setPointSizeF(8.0)
    font.setWeight(QFont.Weight.Bold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    return font


def _caption(parent: QWidget, number: str, text: str) -> QWidget:
    """Index caption: a muted number and a letter-spaced uppercase word, e.g. 01  LINKS."""
    box = QWidget(parent)
    box.setObjectName("monoCaptionBox")
    lay = kit.hbox(spacing=10)
    box.setLayout(lay)
    if number:
        num = QLabel(number, box)
        num.setObjectName("monoNum")
        num.setFont(_caption_font())
        lay.addWidget(num)
    word = QLabel(text, box)
    word.setObjectName("monoCaption")
    word.setFont(_caption_font())
    lay.addWidget(word)
    lay.addStretch(1)
    box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return box


class _Bleed(QWidget):
    """Holds the download list and lets it overhang its left edge by ``bleed`` px (clipped).

    The list's title delegate insets its text by 10px; bleeding the list 10px to the left puts
    the first column exactly on the grid line of the captions above it. The holder also asks
    only for the list's minimum height (the stretch gives it the rest), so the page fits a
    small laptop screen without scrolling.
    """

    def __init__(self, child: QWidget, bleed: int, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("monoBleed")
        self._child = child
        self._bleed = bleed
        child.setParent(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(child.minimumHeight())

    def sizeHint(self) -> QSize:
        return QSize(self._child.sizeHint().width(), self.minimumHeight())

    def minimumSizeHint(self) -> QSize:
        hint = self._child.minimumSizeHint()
        return QSize(max(0, hint.width() - self._bleed), hint.height())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._child.setGeometry(-self._bleed, 0, self.width() + self._bleed, self.height())


def _air(parent: QWidget, low: int, high: int) -> QWidget:
    """Vertical gap that is ``low`` px on small screens and grows up to ``high`` px when the
    window has room (it takes a share of the spare height next to the download list)."""
    gap = QWidget(parent)
    gap.setObjectName("monoAir")
    gap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    gap.setMinimumHeight(low)
    gap.setMaximumHeight(high)
    return gap


def _rule(parent: QWidget, name: str) -> QFrame:
    line = QFrame(parent)
    line.setObjectName(name)
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


# ---------------------------------------------------------------------- layout

def build(w: MainWindow, content: QWidget) -> None:
    root = kit.vbox(spacing=0, margins=(28, 10, 28, 4))
    content.setLayout(root)

    # Masthead: title + tagline on one baseline, Settings on the right, heavy rule.
    head = kit.hbox(w.title_label, 14, w.tagline_label, None, w.settings_button, spacing=0)
    head.setAlignment(w.title_label, Qt.AlignmentFlag.AlignBottom)
    head.setAlignment(w.tagline_label, Qt.AlignmentFlag.AlignBottom)
    root.addLayout(head)
    root.addSpacing(8)
    root.addWidget(_rule(content, "monoRuleHeavy"))
    root.addSpacing(12)

    # The form: index column | field column.
    grid = QGridLayout()
    grid.setHorizontalSpacing(GUTTER)
    grid.setVerticalSpacing(9)
    grid.setColumnStretch(1, 1)

    links_cap = _caption(content, "01", "Links")
    preset_cap = _caption(content, "02", "Preset")
    folder_cap = _caption(content, "03", "Save to")
    options_cap = _caption(content, "04", "Options")

    kit.set_role(w.clear_links_btn, "monoTight")
    links_cap.setContentsMargins(0, 13, 0, 0)  # caption sits on the first line of links
    paste_row = kit.hbox((w.paste_btn, 1), w.clear_links_btn, spacing=4)
    links_col = kit.vbox(links_cap, 8, paste_row, None, spacing=0)
    w.url_edit.setMinimumHeight(76)
    w.url_edit.setMaximumHeight(82)
    grid.addLayout(links_col, 0, 0)
    grid.addWidget(w.url_edit, 0, 1)

    w.preset_combo.setMinimumContentsLength(22)
    grid.addWidget(preset_cap, 1, 0, Qt.AlignmentFlag.AlignVCenter)
    grid.addLayout(kit.hbox((w.preset_combo, 3), (w.preset_hint, 2), spacing=GUTTER), 1, 1)

    grid.addWidget(folder_cap, 2, 0, Qt.AlignmentFlag.AlignVCenter)
    grid.addLayout(kit.hbox((w.folder_edit, 1), w.browse_btn, w.open_folder_btn, spacing=8), 2, 1)

    grid.addWidget(options_cap, 3, 0, Qt.AlignmentFlag.AlignVCenter)
    grid.addLayout(kit.hbox(w.playlist_check, w.subs_check, w.meta_check, None, spacing=24), 3, 1)

    grid.addWidget(w.advanced_panel, 4, 0, 1, 2)
    root.addLayout(grid)
    root.addWidget(_air(content, 10, 16), 1)

    # Action row: Advanced toggle in the index column; the Download slab starts the field
    # column and is exactly as wide as the preset box above it (same split, same gutter).
    action = kit.hbox(w.advanced_toggle, spacing=GUTTER)
    action.addLayout(kit.hbox((w.download_btn, 3), (kit.hbox(None, w.cancel_btn), 2),
                              spacing=GUTTER), 1)
    root.addLayout(action)
    root.addWidget(_air(content, 10, 22), 1)

    # Downloads: a ruled table across the full width, with a footnote line under it.
    root.addWidget(_rule(content, "monoRule"))
    root.addWidget(_Bleed(w.queue, TABLE_INDENT, content), 3)
    root.addSpacing(6)
    root.addWidget(w.banner)
    kit.set_role(w.summary_label, "monoMeta")
    kit.set_role(w.clear_finished_btn, "monoTight")  # text ends on the grid's right edge
    root.addLayout(kit.hbox(
        w.log_toggle, GUTTER, w.summary_label, None, w.clear_finished_btn,
        spacing=0, margins=(0, 2, 0, 0),
    ))
    root.addWidget(w.log_view, 2)
    root.addLayout(kit.hbox(None, w.copy_log_btn, w.clear_log_btn, spacing=4))

    # Queue columns: tighter than the defaults so titles keep room at 820px.
    for column, width in ((1, 136), (2, 138), (3, 90), (4, 58), (5, 130)):
        w.queue.setColumnWidth(column, width)

    # Strict grid: captions, both toggles and the panel's labels share one column width.
    captions = [links_cap, preset_cap, folder_cap, options_cap]
    toggles: list[QWidget] = [w.advanced_toggle, w.log_toggle]
    labels: list[QWidget] = [*captions, *toggles, *w.advanced_panel.labels]
    width = max([INDEX_MIN_WIDTH, *(item.sizeHint().width() for item in labels)])
    for item in labels:
        item.setMinimumWidth(width)
    for item in (*captions, *toggles):
        item.setMaximumWidth(width)


# ------------------------------------------------------------------ stylesheet

def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:
    mono = css_families(MONO)
    display = css_families(DISPLAY)
    ink, paper, signal = t.text, t.card, t.danger
    chevron = glyph("chevron", ink)
    chevron_open = glyph("chevron", signal)
    check = glyph("check", t.on_accent)
    return f"""
QWidget#pageContent {{ background: {t.window}; }}
QFrame#monoRuleHeavy {{ background: {ink}; border: none; min-height: 5px; max-height: 5px; }}
QFrame#monoRule {{ background: {ink}; border: none; min-height: 2px; max-height: 2px; }}
QLabel#monoNum {{ color: {t.muted}; background: transparent; }}
QLabel#monoCaption {{ color: {ink}; background: transparent; }}

QLabel#appTitle {{ font-family: {display}; font-size: {m.app_title_size}pt; font-weight: 800;
    color: {ink}; }}
QLabel#appTagline {{ font-family: {mono}; font-size: 8.5pt; color: {t.muted}; padding-bottom: 3px; }}
QLabel#fieldLabel {{ font-family: {mono}; font-size: 8.5pt; font-weight: 600; color: {t.muted}; }}
QLabel#muted {{ color: {t.muted}; }}
QLabel[role="monoMeta"] {{ font-family: {mono}; font-size: 9pt; color: {t.muted}; }}

/* Buttons: sharp, 2px ink outline; Download is the only filled shape. */
QPushButton {{
    background: {paper}; color: {ink}; border: 2px solid {ink}; border-radius: 0;
    font-weight: 600;
}}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:pressed {{ background: {mix(paper, ink, 0.16)}; }}
QPushButton:focus {{ border-color: {signal}; }}
QPushButton:disabled {{ background: {paper}; color: {t.muted}; border-color: {t.border}; }}
QPushButton#primary {{
    background: {t.accent}; color: {t.on_accent}; border: 2px solid {t.accent}; border-radius: 0;
    font-weight: 700; text-align: left; padding-left: 20px; padding-right: 40px;
}}
QPushButton#primary:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton#primary:pressed {{ background: {t.accent_pressed}; border-color: {t.accent_pressed}; }}
QPushButton#primary:focus {{ border-color: {signal}; }}
QPushButton#primary:disabled {{
    background: {t.accent_disabled}; color: {t.muted}; border-color: {t.accent_disabled};
}}
QPushButton#secondary {{
    border-radius: 0; font-weight: 600; font-size: {m.font_size}pt; padding: 6px 16px;
}}
QPushButton#secondary:disabled {{ border-color: {t.border}; }}
QPushButton#flat {{
    background: transparent; border: none; color: {t.muted}; padding: 4px 6px;
    font-family: {mono}; font-size: 8.5pt; font-weight: 600;
}}
QPushButton#flat:hover {{ color: {ink}; background: {t.accent_soft}; }}
QPushButton#flat:pressed {{ color: {paper}; background: {ink}; }}
QPushButton#flat:disabled {{ color: {mix(t.muted, t.window, 0.45)}; background: transparent; }}
QPushButton#flat[role="monoTight"] {{ padding: 4px 2px; }}
QPushButton#accentSmall {{
    background: {t.accent}; color: {t.on_accent}; border: 2px solid {t.accent};
}}
QPushButton#accentSmall:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}

QToolButton#sectionToggle {{
    font-family: {mono}; font-size: 8.5pt; font-weight: 700; color: {t.muted};
    padding: 4px 4px 4px 0;
}}
QToolButton#sectionToggle:hover, QToolButton#sectionToggle:checked {{ color: {ink}; }}
QToolButton#menuButton {{
    background: {paper}; color: {ink}; border: 2px solid {ink}; border-radius: 0; font-weight: 600;
    padding: 4px 28px 4px 10px;
}}
QToolButton#menuButton:hover {{ background: {t.hover}; }}
QToolButton#menuButton::menu-indicator {{ image: url("{chevron}"); }}

/* Fields */
QLineEdit, QComboBox {{
    background: {paper}; color: {ink}; border: 2px solid {ink}; border-radius: 0;
    selection-background-color: {ink}; selection-color: {paper};
}}
QLineEdit:hover, QComboBox:hover {{ border-color: {ink}; background: {t.alt}; }}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border-color: {signal}; background: {paper}; }}
QLineEdit:disabled, QComboBox:disabled {{ border-color: {t.border}; color: {t.muted}; }}
QComboBox {{ padding-right: 40px; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right; width: 32px;
    border: none; border-left: 2px solid {ink}; background: transparent;
}}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 11px; height: 11px; }}
QComboBox::down-arrow:on {{ image: url("{chevron_open}"); }}
QComboBox QAbstractItemView {{
    background: {paper}; color: {ink}; border: 2px solid {ink}; padding: 0; outline: 0;
    selection-background-color: {t.selection}; selection-color: {ink};
}}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 10px; border-radius: 0; }}

QPlainTextEdit {{
    background: {paper}; color: {ink}; border: 2px solid {ink}; border-radius: 0;
    selection-background-color: {ink}; selection-color: {paper};
}}
QPlainTextEdit:focus {{ border-color: {signal}; }}
QPlainTextEdit#links {{ font-family: {mono}; font-size: 9.5pt; padding: 8px 8px; }}
QPlainTextEdit#links[dragActive="true"] {{ border: 2px dashed {signal}; background: {t.accent_soft}; }}
QPlainTextEdit#log {{ background: {t.alt}; border: 2px solid {ink}; }}

QCheckBox {{ spacing: 9px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border-radius: 0; border: 2px solid {ink}; background: {paper};
}}
QCheckBox::indicator:hover {{ background: {t.hover}; border-color: {ink}; }}
QCheckBox::indicator:focus {{ border-color: {signal}; }}
QCheckBox::indicator:checked {{ background: {ink}; border-color: {ink}; image: url("{check}"); }}
QCheckBox::indicator:checked:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}

/* The downloads table */
QTreeWidget#queue {{
    background: {t.window}; border: none; outline: 0;
    selection-background-color: {t.selection}; selection-color: {ink};
}}
QTreeWidget#queue::item {{ border-bottom: 1px solid {t.border}; padding: 0 4px; }}
QTreeWidget#queue::item:hover {{ background: {t.alt}; }}
QTreeWidget#queue::item:selected {{ background: {t.selection}; color: {ink}; }}
QTreeWidget#queue QHeaderView::section {{
    background: {t.window}; color: {ink}; border: none; border-bottom: 1px solid {ink};
    padding: 7px 8px 7px 10px; font-family: {mono}; font-size: 8.5pt; font-weight: 700;
}}

QFrame#errorBanner {{
    background: {mix(t.card, signal, 0.07)}; border: none; border-left: 6px solid {signal};
    border-radius: 0;
}}

QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ border-radius: 0; }}
QMenu {{ background: {paper}; color: {ink}; border: 2px solid {ink}; padding: 4px; }}
QMenu::item {{ border-radius: 0; }}
QMenu::item:selected {{ background: {t.selection}; color: {ink}; }}
QMenu::separator {{ background: {t.border}; }}
QToolTip {{ background: {ink}; color: {paper}; border: none; border-radius: 0; padding: 6px 8px; }}
QStatusBar {{ background: {t.window}; border-top: 1px solid {t.border}; }}
QStatusBar QLabel {{ font-family: {mono}; font-size: 8.5pt; }}
"""


def after_refresh(w: MainWindow, t: Theme) -> None:
    """Download arrow: on-accent when enabled, muted when greyed out (no links yet)."""
    w.download_btn.setIcon(glyph_icon("download", t.color("on_accent"), t.color("muted")))


VARIANT = Variant(
    key="mono",
    name="Mono",
    description=("Swiss monochrome: ink on white, sharp 2px outlines, monospace captions "
                 "and one signal colour."),
    light=LIGHT,
    dark=DARK,
    metrics=Metrics(
        font_families=SANS,
        font_size=10.0,
        heading_families=DISPLAY,
        mono_families=MONO,
        app_title_size=18.0,
        card_title_size=11.0,
        heading_weight=700,
        card_radius=0,
        control_radius=0,
        primary_radius=0,
        control_padding=(6, 10),
        button_padding=(6, 14),
        primary_padding=(10, 24),
        primary_font_size=11.0,
        border_width=2,
        indicator_size=14,
        scrollbar_width=10,
        row_extra=22,
    ),
    build_layout=build,
    extra_qss=extra_qss,
    after_refresh=after_refresh,
)
