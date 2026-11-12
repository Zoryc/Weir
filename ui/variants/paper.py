"""Paper: a warm editorial page — cream paper, serif masthead, terracotta accent.

No boxed cards. A single centred column reads top to bottom like a printed page:
masthead with a double rule, then small letter-spaced section labels (LINKS,
PRESET, SAVE TO, DOWNLOADS) separated by hairline rules and white space.
Buttons are quiet outlines; only Download is filled, in terracotta.

The downloads list is the one part that grows: it always keeps room for three
rows, the log (when open) stays a fixed, smaller strip, and on short windows the
masthead folds onto one line and the gaps tighten so nothing needs a page scroll.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QLabel,
    QLayout,
    QApplication,
    QSizePolicy,
    QSpacerItem,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from .. import kit
from ..icon import glyph_icon
from ..theme import GlyphFile, Metrics, Theme, Variant, css_families, mix
from ..widgets import repolish

if TYPE_CHECKING:
    from ..main_window import MainWindow

SERIF = ("Georgia", "Noto Serif", "DejaVu Serif", "Times New Roman")
SANS = ("Segoe UI", "Noto Sans", "DejaVu Sans", "Helvetica Neue", "Arial")
COLUMN_MAX_WIDTH = 880  # comfortable reading measure; the page centres it
TIGHT_BELOW = 760  # page height (px) under which the vertical rhythm tightens
NARROW_BELOW = 800  # window width (px) under which side margins and gutters shrink
QUEUE_ROWS = 3  # the downloads list never gets less room than this many rows
LOG_MAX_HEIGHT = 150  # the log is a reference strip; the list gets the rest

LIGHT = Theme(
    dark=False,
    window="#f5efe4",  # cream paper
    card="#fffbf4",  # input wells, a shade lighter than the page
    alt="#efe7d9",
    hover="#ece3d3",
    border="#e1d6c4",  # hairline rules
    border_strong="#c8baa4",
    text="#2a241e",  # ink
    muted="#73665a",
    accent="#ad4e2c",  # terracotta
    accent_hover="#9b4426",
    accent_pressed="#873a20",
    accent_disabled="#e3c4b4",
    accent_soft="#f1dfd2",
    selection="#ecdccb",
    track="#e5dbcc",
    success="#346a3a",
    warning="#82570b",  # deep ochre: readable as pill text on cream
    danger="#9e1f45",  # cool crimson, well apart from the terracotta accent
    scroll="#cdbfaa",
    on_accent="#fffaf3",
)

DARK = Theme(
    dark=True,
    window="#1f1c19",  # warm charcoal
    card="#282420",
    alt="#2c2824",
    hover="#342f2a",
    border="#3a342e",
    border_strong="#4f473e",
    text="#ece3d3",  # cream ink
    muted="#a69a89",
    accent="#d0714b",  # terracotta, lifted for the dark page
    accent_hover="#da7d56",
    accent_pressed="#bf6540",
    accent_disabled="#4b3429",
    accent_soft="#3a2a22",
    selection="#41322a",
    track="#3a342e",
    success="#8cbf80",
    warning="#e0ac4c",
    danger="#f0828e",
    scroll="#564d43",
    on_accent="#fff8ee",  # cream labels sit on the deeper DARK_FILL below, not on this accent
)

# Filled controls (Download, checked boxes, the banner's fix button) in dark mode use a
# deeper terracotta than the accent: cream labels keep 4.8:1 on it, while section labels,
# status pills and progress bars keep the brighter accent that reads on charcoal.
DARK_FILL = ("#b0532f", "#bc5d37", "#9a4627")  # normal, hover, pressed

METRICS = Metrics(
    font_families=SANS,
    font_size=10.0,
    heading_families=SERIF,
    app_title_size=22.0,
    card_title_size=12.0,
    heading_weight=400,
    card_radius=2,
    control_radius=3,
    primary_radius=3,
    control_padding=(6, 9),
    button_padding=(5, 14),
    primary_padding=(10, 30),
    primary_font_size=11.5,
    indicator_size=15,
    scrollbar_width=10,
    row_extra=14,
)


# ------------------------------------------------------------------ helpers

class _Rhythm(QObject):
    """Generous white space on big windows, a tighter rhythm on small ones.

    Short page (height of the scroll area's viewport, which does not depend on the
    content): smaller vertical gaps and link box, and the masthead folds onto one
    line, so a small laptop screen keeps the downloads list in view. Narrow window:
    smaller side margins, gutters and button padding, so nothing is cut off.
    """

    def __init__(self, content: QWidget, column: QWidget) -> None:
        super().__init__(content)
        self._content = content
        self._column = column  # carries tight/narrow properties for the stylesheet
        self._gaps: list[tuple[QSpacerItem, QBoxLayout, int, int]] = []
        self._heights: list[tuple[QWidget, int, int]] = []
        self._side_margins: list[tuple[QBoxLayout, int, int]] = []
        self._gutters: list[tuple[QGridLayout, int, int]] = []
        self._spacings: list[tuple[QBoxLayout, int, int]] = []
        self._folds: list[tuple[QBoxLayout, int, int]] = []
        self._restyle: list[QWidget] = []
        self._roomy_only: list[QWidget] = []
        self._hooks: list[Callable[[bool, bool], None]] = []
        self._state: tuple[bool, bool] | None = None
        content.installEventFilter(self)

    def gap(self, layout: QBoxLayout, roomy: int, tight: int) -> None:
        layout.addSpacing(roomy)
        item = layout.itemAt(layout.count() - 1)
        spacer = item.spacerItem() if item is not None else None
        if spacer is not None:
            self._gaps.append((spacer, layout, roomy, tight))

    def fixed_height(self, widget: QWidget, roomy: int, tight: int) -> None:
        widget.setFixedHeight(roomy)
        self._heights.append((widget, roomy, tight))

    def side_margins(self, layout: QBoxLayout, roomy: int, narrow: int) -> None:
        layout.setContentsMargins(roomy, 0, roomy, 0)
        self._side_margins.append((layout, roomy, narrow))

    def gutter(self, grid: QGridLayout, roomy: int, narrow: int) -> None:
        grid.setHorizontalSpacing(roomy)
        self._gutters.append((grid, roomy, narrow))

    def spacing(self, box: QBoxLayout, roomy: int, narrow: int) -> None:
        """Horizontal spacing of a row that shrinks on narrow windows."""
        box.setSpacing(roomy)
        self._spacings.append((box, roomy, narrow))

    def fold(self, box: QBoxLayout, stacked_spacing: int, inline_spacing: int) -> None:
        """Stack the box's items when roomy; put them on one line when tight."""
        box.setDirection(QBoxLayout.Direction.TopToBottom)
        box.setSpacing(stacked_spacing)
        self._folds.append((box, stacked_spacing, inline_spacing))

    def roomy_only(self, widget: QWidget) -> None:
        """One of our own decorations, dropped on short windows to save height."""
        self._roomy_only.append(widget)

    def restyle(self, *widgets: QWidget) -> None:
        """Widgets styled by QWidget#paperColumn[tight="true"] / [narrow="true"] rules."""
        self._restyle.extend(widgets)

    def on_change(self, hook: Callable[[bool, bool], None]) -> None:
        """Called with (tight, narrow) after every mode change, once styles are applied."""
        self._hooks.append(hook)

    def owns(self, widget: QWidget) -> bool:
        return self._content.isAncestorOf(widget)  # not re-used by a newer layout

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.ParentChange):
            try:
                self._update()
            except RuntimeError:  # the layout is being torn down
                pass
        return False

    def _update(self) -> None:
        page = self._content.parentWidget()
        if page is None:
            return
        # Viewport height and window width: neither changes when the page's own
        # scrollbar comes and goes, so the modes cannot flip back and forth.
        state = (page.height() < TIGHT_BELOW, self._content.window().width() < NARROW_BELOW)
        if state == self._state:
            return
        self._state = tight, narrow = state
        layouts: list[QLayout] = []
        for spacer, layout, roomy, small in self._gaps:
            spacer.changeSize(0, small if tight else roomy,
                              QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            if layout not in layouts:
                layouts.append(layout)
        for box, roomy, small in self._side_margins:
            box.setContentsMargins(small if narrow else roomy, 0, small if narrow else roomy, 0)
        for grid, roomy, small in self._gutters:
            grid.setHorizontalSpacing(small if narrow else roomy)
            layouts.append(grid)
        for box, roomy, small in self._spacings:
            box.setSpacing(small if narrow else roomy)
        for box, stacked, inline in self._folds:
            box.setDirection(QBoxLayout.Direction.LeftToRight if tight else QBoxLayout.Direction.TopToBottom)
            box.setSpacing(inline if tight else stacked)
        for widget, roomy, small in self._heights:
            if self.owns(widget):
                widget.setFixedHeight(small if tight else roomy)
        for widget in self._roomy_only:
            widget.setVisible(not tight)
        self._column.setProperty("tight", tight)
        self._column.setProperty("narrow", narrow)
        repolish(self._column)
        for widget in self._restyle:
            if self.owns(widget):
                repolish(widget)
                # New padding/fonts: let the widget re-measure (scroll areas re-lay their viewport).
                QApplication.sendEvent(widget, QEvent(QEvent.Type.StyleChange))
                widget.updateGeometry()
        for hook in self._hooks:
            hook(tight, narrow)
        for layout in layouts:
            layout.invalidate()


def _kicker(text: str, parent: QWidget) -> QLabel:
    """Small, upper-case, letter-spaced section label."""
    label = QLabel(text, parent)
    label.setObjectName("paperKicker")
    return label


def _rule(parent: QWidget, name: str = "paperRule") -> QFrame:
    line = QFrame(parent)
    line.setObjectName(name)
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return line


def _double_rule(parent: QWidget) -> QVBoxLayout:
    """Masthead rule: a heavy ink line over a hairline, like a newspaper."""
    return kit.vbox(_rule(parent, "paperRuleHeavy"), _rule(parent, "paperRuleInk"), spacing=2)


class _Rows(QWidget):
    """Holds the downloads list and asks for exactly the header plus a few rows.

    When the page is taller than everything needs, the list's stretch factor hands
    it the rest. When it is not (small window, Advanced options or Log open), the
    scroll area lays the page out at preferred sizes — this keeps the list at three
    full rows there instead of its 130 px default minimum.
    """

    def __init__(self, queue: QTreeWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self._queue = queue
        self.setLayout(kit.vbox(queue, spacing=0))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _height_for(self, rows: int) -> int:
        queue = self._queue
        row = queue.sizeHintForRow(0)
        if row <= 0:  # empty list: the same formula the row delegates use
            row = queue.fontMetrics().height() * 2 + METRICS.row_extra
        header = queue.header().sizeHint().height() if not queue.isHeaderHidden() else 0
        return header + rows * row + 2 * queue.frameWidth() + 1

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().sizeHint().width(), self._height_for(QUEUE_ROWS))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(super().minimumSizeHint().width(), self._height_for(QUEUE_ROWS - 1))


# ------------------------------------------------------------------- layout

def build(w: MainWindow, content: QWidget) -> None:
    outer = kit.hbox(spacing=0)
    content.setLayout(outer)
    column = QWidget(content)
    column.setObjectName("paperColumn")
    rhythm = _Rhythm(content, column)
    rhythm.side_margins(outer, 28, 18)
    column.setMaximumWidth(COLUMN_MAX_WIDTH)
    column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    outer.addStretch(1)
    outer.addWidget(column, 100)
    outer.addStretch(1)

    col = kit.vbox(spacing=0)
    column.setLayout(col)
    rhythm.gap(col, 14, 5)

    # Masthead: title over an italic tagline; on one line when the window is short
    names = kit.vbox(spacing=0)
    names.addWidget(w.title_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
    names.addWidget(w.tagline_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
    names.addStretch(1)
    rhythm.fold(names, 0, 14)
    masthead = kit.hbox(spacing=16)
    masthead.addLayout(names, 1)
    masthead.setAlignment(names, Qt.AlignmentFlag.AlignVCenter)
    masthead.addWidget(w.settings_button, 0, Qt.AlignmentFlag.AlignVCenter)
    w.settings_button.setIconSize(QSize(18, 18))
    col.addLayout(masthead)
    rhythm.gap(col, 10, 5)
    col.addLayout(_double_rule(content))
    rhythm.gap(col, 16, 10)

    # 1 · Links: the box with its two actions in the margin, like Browse…/Open below
    links_label = _kicker("Links", content)
    rhythm.roomy_only(links_label)  # short windows: the tagline and Paste button say it already
    col.addWidget(links_label)
    rhythm.gap(col, 6, 0)
    rhythm.fixed_height(w.url_edit, 88, 60)  # three lines of links; two on short screens
    link_actions = kit.vbox(w.paste_btn, w.clear_links_btn, None, spacing=2)
    col.addLayout(kit.hbox((w.url_edit, 1), link_actions, spacing=8))
    rhythm.gap(col, 18, 8)

    # 2 · Preset + folder, side by side --------------------------------------
    form = QGridLayout()
    rhythm.gutter(form, 28, 18)
    form.setVerticalSpacing(5)
    form.addWidget(_kicker("Preset", content), 0, 0)
    form.addWidget(_kicker("Save to", content), 0, 1)
    w.preset_combo.setMinimumContentsLength(18)
    form.addWidget(w.preset_combo, 1, 0)
    form.addLayout(kit.hbox((w.folder_edit, 1), w.browse_btn, w.open_folder_btn, spacing=6), 1, 1)
    kit.set_role(w.preset_hint, "paperCaption")
    form.addWidget(w.preset_hint, 2, 0, 1, 2, Qt.AlignmentFlag.AlignTop)  # a caption line under the row
    form.setColumnStretch(0, 9)  # the folder path needs a little more room than the preset
    form.setColumnStretch(1, 11)
    col.addLayout(form)
    rhythm.gap(col, 12, 7)

    # Options, with "Advanced options" at the end of the row: its panel opens
    # right underneath, before the rule, so it reads as part of the options.
    options = kit.hbox(w.playlist_check, w.subs_check, w.meta_check, None)
    rhythm.spacing(options, 26, 16)
    options.addWidget(w.advanced_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
    col.addLayout(options)
    w.advanced_panel.setContentsMargins(0, 10, 0, 4)  # breathing room only while it is open
    col.addWidget(w.advanced_panel)
    rhythm.gap(col, 12, 9)
    options_rule = _rule(content)
    rhythm.roomy_only(options_rule)  # short windows: white space alone separates
    col.addWidget(options_rule)
    rhythm.gap(col, 12, 0)

    # 3 · Download ---------------------------------------------------------
    w.download_btn.setMinimumWidth(200)
    col.addLayout(kit.hbox(None, w.cancel_btn, w.download_btn, spacing=12))
    rhythm.gap(col, 18, 8)

    # Downloads ------------------------------------------------------------
    col.addWidget(_rule(content, "paperRuleInk"))
    rhythm.gap(col, 8, 3)
    kit.set_role(w.summary_label, "paperCaption")
    col.addLayout(kit.hbox(
        _kicker("Downloads", content), 14, w.summary_label, None, w.clear_finished_btn, spacing=0,
    ))
    for column_index, width in ((1, 140), (2, 124), (3, 84), (4, 54), (5, 122)):
        w.queue.setColumnWidth(column_index, width)
    col.addWidget(_Rows(w.queue, content), 1)  # the list takes whatever room is left
    rhythm.gap(col, 8, 5)
    col.addWidget(w.banner)
    rhythm.gap(col, 4, 2)
    col.addLayout(kit.hbox(w.log_toggle, None, w.copy_log_btn, w.clear_log_btn, spacing=4))
    w.log_view.setMaximumHeight(LOG_MAX_HEIGHT)
    col.addWidget(w.log_view)  # no stretch: the list ranks above the log
    rhythm.gap(col, 8, 2)

    for edge in (w.clear_finished_btn, w.clear_log_btn):
        kit.set_role(edge, "paperEdge")  # text ends on the column's right edge
    for slim in (w.browse_btn, w.open_folder_btn):
        kit.set_role(slim, "paperSlim")  # matches the folder field's height; slimmer when narrow
    rhythm.restyle(w.title_label, w.tagline_label, w.download_btn, w.cancel_btn, w.browse_btn,
                   w.open_folder_btn, w.url_edit, w.clear_finished_btn, w.clear_links_btn,
                   w.copy_log_btn, w.clear_log_btn, w.advanced_toggle, w.log_toggle, w.settings_button)

    def align_tagline(tight: bool, _narrow: bool) -> None:
        # One line: sit the tagline on the title's baseline (both are bottom-aligned).
        if rhythm.owns(w.tagline_label):
            lift = w.title_label.fontMetrics().descent() - w.tagline_label.fontMetrics().descent()
            w.tagline_label.setContentsMargins(0, 0, 0, max(0, lift) if tight else 0)

    rhythm.on_change(align_tagline)
    kit.align_labels(w.advanced_panel.labels)


# --------------------------------------------------------------- stylesheet

def _fills(t: Theme) -> tuple[str, str, str]:
    """Background of filled controls: normal, hover, pressed."""
    return DARK_FILL if t.dark else (t.accent, t.accent_hover, t.accent_pressed)


def _disabled_label(t: Theme) -> str:
    """A disabled Download must still read "Download" (it is the first thing a new
    user sees): deeper terracotta on the pale fill in light mode, soft cream on dark."""
    return mix(t.accent_disabled, t.text if t.dark else t.accent_pressed, 0.62 if t.dark else 0.7)


def after_refresh(w: MainWindow, t: Theme) -> None:
    """Match the disabled Download icon to its label colour."""
    w.download_btn.setIcon(glyph_icon("download", t.color("on_accent"), QColor(_disabled_label(t))))


def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:
    serif = css_families(SERIF)
    kicker = "font-size: 8pt; font-weight: 700; letter-spacing: 1.6px; text-transform: uppercase;"
    well = t.card
    disabled_label = _disabled_label(t)
    fill, fill_hover, fill_pressed = _fills(t)
    # Section labels: a deeper terracotta on cream so 8pt text stays readable (5.6:1).
    kicker_color = t.accent if t.dark else t.accent_hover
    return f"""
QWidget#pageContent, QWidget#paperColumn {{ background: {t.window}; }}

/* Masthead */
QLabel#appTitle {{ font-family: {serif}; font-size: {m.app_title_size}pt; font-weight: 400;
    color: {t.text}; letter-spacing: 0.3px; }}
QLabel#appTagline {{ font-family: {serif}; font-style: italic; font-size: {m.font_size + 0.5}pt;
    color: {t.muted}; }}
QFrame#paperRuleHeavy {{ background: {t.text}; border: none; min-height: 2px; max-height: 2px; }}
QFrame#paperRuleInk {{ background: {mix(t.window, t.text, 0.55)}; border: none; min-height: 1px; max-height: 1px; }}
QFrame#paperRule {{ background: {t.border}; border: none; min-height: 1px; max-height: 1px; }}

/* Section labels and captions */
QLabel#paperKicker {{ color: {kicker_color}; {kicker} }}
QLabel#fieldLabel {{ color: {t.muted}; {kicker} }}
QLabel[role="paperCaption"] {{ font-family: {serif}; font-style: italic; color: {t.muted};
    font-size: {m.font_size + 0.5}pt; }}
/* Toggles: the chevron's own inset is pulled back so it sits on the column's left edge */
QToolButton#sectionToggle {{ color: {t.muted}; {kicker} padding: 3px 0 3px 0;
    margin-left: -4px; margin-right: -10px; }}
QToolButton#sectionToggle:hover, QToolButton#sectionToggle:checked:hover {{ color: {kicker_color}; }}
QToolButton#sectionToggle:checked {{ color: {t.text}; }}

/* Quiet controls */
QPushButton {{ background: transparent; border: 1px solid {t.border_strong}; color: {t.text}; }}
QPushButton:hover {{ background: {t.hover}; border-color: {t.muted}; }}
QPushButton:pressed {{ background: {t.alt}; }}
QPushButton:disabled {{ background: transparent; color: {t.muted}; border-color: {t.border}; }}
QPushButton[role="paperSlim"] {{ min-height: 21px; }}
QPushButton#flat {{ background: transparent; border: none; color: {t.muted}; padding: 4px 8px; }}
QPushButton#flat:hover {{ background: transparent; color: {kicker_color}; text-decoration: underline; }}
QPushButton#flat:disabled {{ color: {mix(t.muted, t.window, 0.4)}; }}
QPushButton#flat[role="paperEdge"] {{ padding-right: 0; }}
QPushButton#secondary {{ background: transparent; border: 1px solid {t.border_strong}; color: {t.text}; }}
QPushButton#secondary:hover {{ background: {t.hover}; border-color: {t.muted}; }}
QPushButton#secondary:disabled {{ color: {t.muted}; border-color: {t.border}; background: transparent; }}

/* The one filled button */
QPushButton#primary {{
    background: {fill}; color: {t.on_accent}; border: 1px solid {fill};
    font-family: {serif}; font-size: {m.primary_font_size}pt; font-weight: 700; letter-spacing: 0.4px;
}}
QPushButton#primary:hover {{ background: {fill_hover}; border-color: {fill_hover}; }}
QPushButton#primary:pressed {{ background: {fill_pressed}; border-color: {fill_pressed}; }}
QPushButton#primary:disabled {{ background: {t.accent_disabled}; border-color: {t.accent_disabled};
    color: {disabled_label}; }}

QToolButton#menuButton {{ background: transparent; border: 1px solid transparent; color: {t.text};
    padding: 5px 6px 5px 10px; }}  /* the style already reserves room for the arrow */
QToolButton#menuButton::menu-indicator {{ right: 3px; width: 9px; height: 9px; }}
QToolButton#menuButton:hover {{ background: transparent; border-color: {t.border_strong}; }}

/* Inputs: lighter wells with square-ish corners */
QLineEdit, QComboBox {{ background: {well}; border-color: {t.border_strong}; }}
QPlainTextEdit#links {{ background: {well}; border: 1px solid {t.border_strong}; border-radius: 2px;
    padding: 6px 6px; }}
QPlainTextEdit#links:focus, QLineEdit:focus, QComboBox:focus {{ border-color: {t.accent}; }}
QPlainTextEdit#log {{ background: {t.alt}; border: 1px solid {t.border}; border-radius: 2px; }}

QCheckBox::indicator {{ background: {well}; border-radius: 2px; }}
QCheckBox::indicator:checked {{ background: {fill}; border-color: {fill}; }}
QCheckBox::indicator:checked:hover {{ background: {fill_hover}; border-color: {fill_hover}; }}
QLineEdit, QComboBox, QPlainTextEdit {{ selection-background-color: {fill}; }}

/* Downloads: a printed table, no box */
QTreeWidget#queue {{ background: {t.window}; border: none; }}
QTreeWidget#queue::item {{ border-bottom: 1px solid {t.border}; }}
QTreeWidget#queue::item:hover {{ background: {mix(t.window, t.text, 0.03)}; }}
QTreeWidget#queue::item:selected {{ background: {t.selection}; color: {t.text}; }}
QTreeWidget#queue QHeaderView {{ font-size: 8pt; }}
QTreeWidget#queue QHeaderView::section {{
    background: {t.window}; color: {t.muted}; border: none; border-bottom: 1px solid {t.border_strong};
    padding: 4px 8px 4px 10px; {kicker}
}}

QFrame#errorBanner {{ background: {mix(t.window, t.danger, 0.08)}; border: none;
    border-left: 3px solid {t.danger}; border-radius: 0; }}
QFrame#errorBanner QLabel {{ font-size: {m.font_size - 0.5}pt; }}
QFrame#errorBanner QPushButton {{ padding: 4px 10px; }}
QFrame#errorBanner QPushButton#accentSmall {{ background: {fill}; border: 1px solid {fill}; }}
QFrame#errorBanner QPushButton#accentSmall:hover {{ background: {fill_hover}; border-color: {fill_hover}; }}
QFrame#errorBanner QPushButton#flat {{ padding: 4px 6px; }}

/* Short windows: a smaller masthead, slimmer buttons and link box */
QWidget#paperColumn[tight="true"] QLabel#appTitle {{ font-size: {m.app_title_size - 3}pt; }}
QWidget#paperColumn[tight="true"] QPushButton#primary {{ padding: 6px 26px; }}
QWidget#paperColumn[tight="true"] QPushButton#secondary {{ padding: 6px 18px; }}
QWidget#paperColumn[tight="true"] QToolButton#menuButton {{ padding-top: 3px; padding-bottom: 3px; }}
QWidget#paperColumn[tight="true"] QPlainTextEdit#links {{ padding: 3px 6px; }}
QWidget#paperColumn[tight="true"] QPushButton#flat {{ padding-top: 2px; padding-bottom: 2px; }}
QWidget#paperColumn[tight="true"] QToolButton#sectionToggle {{ padding-top: 1px; padding-bottom: 1px; }}
QWidget#paperColumn[narrow="true"] QPushButton[role="paperSlim"] {{ padding-left: 9px; padding-right: 9px; }}

QMenu {{ border: 1px solid {t.border_strong}; }}
QMenu::item {{ border-radius: 2px; }}
QToolTip {{ border-radius: 0; }}
"""


VARIANT = Variant(
    key="paper",
    name="Paper",
    description="A warm editorial page: cream paper, serif headings, hairline rules and a terracotta accent.",
    light=LIGHT,
    dark=DARK,
    metrics=METRICS,
    build_layout=build,
    extra_qss=extra_qss,
    after_refresh=after_refresh,
)
