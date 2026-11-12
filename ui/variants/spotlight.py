"""Spotlight: a search-engine-like hero.

A friendly question, one big rounded link box and a pill-shaped Download button sit
centred in a soft violet glow; preset, folder and options follow as a compact form whose
labels share one column with the Advanced card (the options are chips, with "Advanced
options" as the last, ghost chip that opens its card right below), and the downloads list
sits below in a clean card. Violet-to-magenta accent.

Responsive: on shorter windows (small laptops) the question gets smaller and the hero
tightens up, and on really short ones the question is tucked away, so the downloads list
keeps its room; on narrow windows the fields and the list columns get slimmer so the
folder path and the download titles stay readable.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPlainTextEdit,
    QSizePolicy,
    QWidget,
)

from .. import kit
from ..icon import glyph_icon, glyph_pixmap
from ..theme import GlyphFile, Metrics, Theme, Variant, current, metrics, mix
from ..widgets import repolish

if TYPE_CHECKING:
    from ..main_window import MainWindow

LIGHT = Theme(
    dark=False,
    window="#faf9fe",
    card="#ffffff",
    alt="#f7f5fd",
    hover="#f2eefc",
    border="#e9e4f4",
    border_strong="#d6cee9",
    text="#1f1b2e",
    muted="#6c6685",
    accent="#7c3aed",
    accent_hover="#6d2ee0",
    accent_pressed="#5b21b6",
    accent_disabled="#a78bea",
    accent_soft="#f1eafe",
    selection="#ece3fd",
    track="#ece8f4",
    # Deep enough to read as small bold text on the card and on the status pills.
    success="#15803d",
    warning="#a15c00",
    danger="#c42a45",
    scroll="#d0c8e2",
)

DARK = Theme(
    dark=True,
    window="#15131d",
    card="#1e1b29",
    alt="#24212f",
    hover="#2b2739",
    border="#2f2b3e",
    border_strong="#443d58",
    text="#eceaf4",
    muted="#a19bb6",
    # Light enough to read as text (the "Downloading" pill, links); surfaces that carry
    # white text use the deeper SURFACE_DARK violet instead (see extra_qss).
    accent="#9d78f8",
    accent_hover="#a888f9",
    accent_pressed="#8a62f2",
    accent_disabled="#3b3163",
    accent_soft="#2a2242",
    selection="#33295a",
    track="#2f2b3d",
    success="#4cc584",
    warning="#f2b54e",
    danger="#ff6b84",
    scroll="#4a4460",
)

# Second colour of the accent family (the end of the violet → magenta gradient). Both are
# deep enough for white text (≥ 4.7:1).
MAGENTA_LIGHT = "#c026d3"
MAGENTA_DARK = "#b13ad0"
# Dark mode: violet for surfaces that carry white text (Download, small accent buttons,
# text selection) — normal, hover, pressed; ≥ 4.5:1 with white, unlike the lighter accent.
SURFACE_DARK = ("#7c4dee", "#8452ef", "#6e40dd")

MAX_WIDTH = 920  # the whole page
BOX_WIDTH = 720  # the link box and the question above it
COMPACT_BELOW = 740  # page height (px) under which the hero tightens up, question smaller
TUCKED_BELOW = 640  # page height (px) under which the question is tucked away
WIDE_FROM = 960  # page width (px) from which fields and list columns get their roomy sizes
EDGE = 19  # inner left/right edge shared by the field rows, the advanced card and the list
HEADLINE = "What would you like to <span style='color:{accent}'>download</span>?"

# Queue column widths (Preset, Progress, Speed, ETA, Status) on narrow and on roomy pages
# (blended in between, see fit_columns); Title takes the rest.
WIDE_COLUMNS = ((1, 186), (2, 140), (3, 86), (4, 60), (5, 128))
NARROW_COLUMNS = ((1, 100), (2, 106), (3, 80), (4, 60), (5, 118))


def _magenta(t: Theme) -> str:
    return MAGENTA_DARK if t.dark else MAGENTA_LIGHT


def _surface(t: Theme) -> tuple[str, str, str]:
    """Violet (normal, hover, pressed) for surfaces with white text on them."""
    return SURFACE_DARK if t.dark else (t.accent, t.accent_hover, t.accent_pressed)


def _accent_text(t: Theme) -> str:
    """Accent for text and icons on tinted surfaces: a touch lighter in dark mode."""
    return mix(t.accent, "#ffffff", 0.15) if t.dark else t.accent


def _restyle(widget: QWidget) -> None:
    """Re-apply the stylesheet after a property change *and* drop the size hints that
    layouts cached for the old padding (repolish alone keeps them)."""
    repolish(widget)
    widget.updateGeometry()


def _centered(widget: QWidget, max_width: int) -> QHBoxLayout:
    """Row that gives ``widget`` all the width up to ``max_width`` and centres it."""
    widget.setMaximumWidth(max_width)
    return kit.hbox(None, (widget, 1000), None, spacing=0)


def _link_box_height(edit: QPlainTextEdit, lines: int) -> int:
    """Height that shows ``lines`` full lines even while the horizontal scrollbar is up
    (links never wrap, so any long link brings that scrollbar)."""
    edit.ensurePolished()
    edit.horizontalScrollBar().ensurePolished()
    frame = edit.contentsMargins()  # border + padding from the stylesheet
    text = lines * edit.fontMetrics().lineSpacing() + 2 * edit.document().documentMargin()
    bar = edit.horizontalScrollBar().sizeHint().height()
    # + 3: QPlainTextEdit wants a pixel or two beyond the last line before it drops its
    # vertical scrollbar (it would otherwise scroll the first link out of view).
    return math.ceil(text) + frame.top() + frame.bottom() + bar + 3


def _queue_rows_height(w: MainWindow, rows: int) -> int:
    """Height of the list header plus ``rows`` download rows."""
    row = QFontMetrics(w.queue.font()).height() * 2 + metrics().row_extra
    return w.queue.header().sizeHint().height() + rows * row + 4


def _hint(item: QLayoutItem) -> QSize:
    """Size hint straight from the widget. QWidgetItem caches its hint until the widget
    calls updateGeometry(), which a stylesheet change does not always do — a stale hint
    would keep a chip at its old (narrower) padding and cut its text."""
    widget = item.widget()
    if widget is None:
        return item.sizeHint()
    return widget.sizeHint().expandedTo(widget.minimumSize()).boundedTo(widget.maximumSize())


class _FlowRow(QLayout):
    """Items side by side from the left edge, wrapping onto a further line when the row
    is full (narrow windows, wider fonts). ``separators`` are dropped at line ends."""

    def __init__(self, spacing: int, line_spacing: int, separators: tuple[QWidget, ...] = ()) -> None:
        super().__init__()
        self._items: list[QLayoutItem] = []
        self._gap = spacing
        self._line_gap = line_spacing
        self._separators = separators
        self._height = 0  # height needed at the current width (see minimumSize)
        self.setContentsMargins(0, 0, 0, 0)

    def set_gap(self, spacing: int) -> None:
        if spacing != self._gap:
            self._gap = spacing
            self.invalidate()

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt API
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._place(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        height = self._place(rect, apply=True)
        if height != self._height:  # wrapped or unwrapped: let the parents make room
            self._height = height
            self.invalidate()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hints = [_hint(item) for item in self._items if not item.isEmpty()]
        width = sum(h.width() for h in hints) + self._gap * max(0, len(hints) - 1)
        return QSize(width, max((h.height() for h in hints), default=0))

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt API
        # Box layouts size the page from minimum sizes, which know nothing about
        # wrapping: report the height the lines need at the width we were last given.
        hints = [_hint(item) for item in self._items if not item.isEmpty()]
        one_line = max((h.height() for h in hints), default=0)
        return QSize(max((h.width() for h in hints), default=0), max(one_line, self._height))

    def _is_separator(self, item: QLayoutItem) -> bool:
        return item.widget() is not None and item.widget() in self._separators

    def _lines(self, width: int) -> list[list[QLayoutItem]]:
        lines: list[list[QLayoutItem]] = []
        line: list[QLayoutItem] = []
        used = 0
        for item in (item for item in self._items if not item.isEmpty()):
            needed = _hint(item).width()
            if line and used + self._gap + needed > width:
                lines.append(line)
                line, used = [], 0
            used = used + self._gap + needed if line else needed
            line.append(item)
        if line:
            lines.append(line)
        for line in lines:  # a divider never starts or ends a line
            while line and self._is_separator(line[0]):
                line.pop(0)
            while line and self._is_separator(line[-1]):
                line.pop()
        return [line for line in lines if line]

    def _place(self, rect: QRect, apply: bool) -> int:
        lines = self._lines(rect.width())
        if apply:
            shown = {id(item) for line in lines for item in line}
            for item in self._items:
                if id(item) not in shown:
                    item.setGeometry(QRect(rect.topLeft(), QSize(0, 0)))
        y = rect.y()
        for line in lines:
            hints = [_hint(item) for item in line]
            line_height = max(h.height() for h in hints)
            x = rect.x()
            for item, hint in zip(line, hints):
                if apply:
                    item.setGeometry(QRect(QPoint(x, y + (line_height - hint.height()) // 2), hint))
                x += hint.width() + self._gap
            y += line_height + self._line_gap
        return max(0, y - self._line_gap - rect.y())


class _Rule(QFrame):
    """Thin vertical divider between the option chips and "Advanced options"."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("chipRule")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(1, 20)


class _FocusRing(QObject):
    """Mirrors the link box's keyboard focus onto the frame around it (QSS has no :focus-within)."""

    def __init__(self, frame: QWidget, editor: QWidget) -> None:
        super().__init__(frame)
        self._frame = frame
        frame.setProperty("focused", editor.hasFocus())
        editor.installEventFilter(self)  # removed automatically when this object dies

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        kind = event.type()
        if kind in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._frame.setProperty("focused", kind == QEvent.Type.FocusIn)
            repolish(self._frame)
        return False


class _BannerFit(QObject):
    """Keeps the error banner at least as tall as its wrapped text needs.

    Box layouts only know the banner's small minimum size, so on short windows they would
    squeeze it and cut the message off; with this the page scrolls a little instead.
    """

    def __init__(self, owner: QObject, banner: QWidget) -> None:
        super().__init__(owner)
        self._banner = banner
        banner.installEventFilter(self)  # removed automatically when this object dies

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show):
            banner = self._banner
            if banner.isVisible() and banner.width() > 0:
                needed = banner.heightForWidth(banner.width())
                if needed > 0 and needed != banner.minimumHeight():
                    banner.setMinimumHeight(needed)
        return False


class _ShownHidden(QObject):
    """Calls ``changed()`` whenever one of ``widgets`` is shown or hidden (by its toggle)."""

    def __init__(self, owner: QObject, widgets: tuple[QWidget, ...], changed: Callable[[], None]) -> None:
        super().__init__(owner)
        self._changed = changed
        for widget in widgets:
            widget.installEventFilter(self)  # removed automatically when this object dies

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() in (QEvent.Type.ShowToParent, QEvent.Type.HideToParent):
            self._changed()
        return False


def _height_tier(height: int) -> int:
    """0 = roomy hero, 1 = tighter hero with a smaller question, 2 = question tucked away."""
    return 0 if height >= COMPACT_BELOW else 1 if height >= TUCKED_BELOW else 2


class _Responsive(QObject):
    """Calls ``apply(tier, wide)`` whenever the page crosses one of the size thresholds,
    and ``fit(width)`` on every width change (cheap: list column widths only).

    Decided on the scroll area's viewport size only (not on the content's own size), so
    switching modes can never feed back into another switch.
    """

    def __init__(self, content: QWidget, apply: Callable[[int, bool], None],
                 fit: Callable[[int], None]) -> None:
        super().__init__(content)
        self._apply = apply
        self._fit = fit
        self._state: tuple[int, bool] | None = None
        self._width = -1
        content.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Resize and isinstance(watched, QWidget):
            viewport = watched.parentWidget() or watched
            state = (_height_tier(viewport.height()), viewport.width() >= WIDE_FROM)
            if state != self._state:
                self._state = state
                self._apply(*state)
            if viewport.width() != self._width:
                self._width = viewport.width()
                self._fit(self._width)
        return False


def build(w: MainWindow, content: QWidget) -> None:
    outer = kit.hbox(spacing=0, margins=(22, 0, 22, 0))
    content.setLayout(outer)
    column = QWidget(content)
    column.setObjectName("spotColumn")
    column.setMaximumWidth(MAX_WIDTH)
    outer.addStretch(1)
    outer.addWidget(column, 1000)
    outer.addStretch(1)

    root = kit.vbox(spacing=12, margins=(0, 10, 0, 6))
    column.setLayout(root)

    # Light top row: violet mark + name + how-it-works … Settings
    mark = QLabel(column)
    mark.setObjectName("spotMark")
    mark.setFixedSize(26, 26)
    mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dot = kit.muted("·", column)
    root.addLayout(kit.hbox(
        mark, 2, w.title_label, 4, dot, 4, w.tagline_label, None, w.settings_button, spacing=6,
    ))

    # Hero: the question, the link box, the big Download button
    hero = kit.panel(column, "hero")
    hlay = kit.vbox(spacing=0)
    hero.setLayout(hlay)

    headline = QLabel(hero)
    headline.setObjectName("heroTitle")
    headline.setTextFormat(Qt.TextFormat.RichText)
    headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
    headline.setContentsMargins(0, 0, 0, 12)
    hlay.addWidget(headline)

    box = kit.panel(hero, "searchBox")
    box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    blay = kit.hbox(spacing=8)
    box.setLayout(blay)
    blay.addWidget(w.url_edit, 1)
    kit.set_role(w.paste_btn, "spotPaste")
    blay.addLayout(kit.vbox(w.paste_btn, w.clear_links_btn, None, spacing=2))
    _FocusRing(box, w.url_edit)
    hlay.addLayout(_centered(box, BOX_WIDTH))

    w.download_btn.setMinimumWidth(240)  # heights of both buttons: see extra_qss
    kit.set_role(w.download_btn, "spotlight")
    kit.set_role(w.cancel_btn, "ghost")
    actions = kit.hbox(None, w.download_btn, 10, w.cancel_btn, None, spacing=0)
    hlay.addLayout(actions)
    root.addWidget(hero)

    # Settings strip: a small form whose label column is shared with the Advanced card, so
    # labels and fields line up all the way down. Wide pages: Preset … Save to … in one row,
    # the preset's description under the combo, then "Options" with the chips ("Advanced
    # options" last, its card opens right below). Narrow pages: Preset + description, then
    # Save to, then the chips from the label edge, so neither the preset name nor the path
    # is cut and the chips still fit one line at 820 px.
    strip = kit.panel(column, "strip")
    slay = kit.vbox(spacing=10)
    strip.setLayout(slay)

    w.folder_edit.setMinimumWidth(150)
    preset_label = kit.field_label("Preset", strip)
    folder_label = kit.field_label("Save to", strip)
    options_label = kit.field_label("Options", strip)
    for label in (preset_label, folder_label, options_label):
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    folder_box = QWidget(strip)
    folder_box.setLayout(kit.hbox((w.folder_edit, 1), w.browse_btn, w.open_folder_btn, spacing=6))
    grid = QGridLayout()
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(6)
    grid.setContentsMargins(EDGE, 0, EDGE, 0)
    slay.addLayout(grid)

    def arrange(wide: bool) -> None:
        for item in (preset_label, w.preset_combo, folder_label, folder_box, w.preset_hint):
            grid.removeWidget(item)
        grid.addWidget(preset_label, 0, 0)
        grid.addWidget(w.preset_combo, 0, 1)
        if wide:
            grid.addWidget(folder_label, 0, 2)
            grid.addWidget(folder_box, 0, 3)
            grid.addWidget(w.preset_hint, 1, 1, 1, 3)
            stretches = (0, 4, 0, 5)
        else:
            grid.addWidget(w.preset_hint, 0, 2, 1, 2)
            grid.addWidget(folder_label, 1, 0)
            grid.addWidget(folder_box, 1, 1, 1, 3)
            stretches = (0, 0, 1, 1)
        for index, stretch in enumerate(stretches):
            grid.setColumnStretch(index, stretch)
        # One label column with the advanced card (its fields sit 16 px after the labels,
        # ours 10 px, hence + 6): same left edge for labels, same left edge for fields.
        kit.align_labels(w.advanced_panel.labels)
        column_width = max((label.minimumWidth() for label in w.advanced_panel.labels), default=0) + 6
        column_width = max(column_width, *(label.sizeHint().width() for label in (preset_label, options_label)))
        preset_label.setMinimumWidth(column_width)
        folder_label.setMinimumWidth(0 if wide else column_width)
        folder_label.setContentsMargins(8 if wide else 0, 0, 0, 0)
        options_label.setFixedWidth(column_width)
        options_label.setVisible(wide)
        # Wide: the combo shares the row with the folder (capped, text fits at 320 px).
        # Narrow: the combo asks for room for a whole preset name; the description wraps.
        w.preset_combo.setMinimumContentsLength(14 if wide else 28)
        w.preset_combo.setMaximumWidth(320)

    for check in (w.playlist_check, w.subs_check, w.meta_check):
        kit.set_role(check, "chip")
    kit.set_role(w.advanced_toggle, "chipToggle")
    chip_parts = (w.playlist_check, w.subs_check, w.meta_check, w.advanced_toggle)
    rule = _Rule(strip)
    chips = _FlowRow(8, 8, separators=(rule,))
    for widget in (w.playlist_check, w.subs_check, w.meta_check, rule, w.advanced_toggle):
        chips.addWidget(widget)
    chips_row = kit.hbox(spacing=10, margins=(EDGE, 0, EDGE, 0))
    chips_row.addWidget(options_label, 0, Qt.AlignmentFlag.AlignTop)  # beside the first line
    chips_row.addLayout(chips, 1)
    slay.addLayout(chips_row)

    adv_card = kit.panel(strip, "advCard")
    alay = kit.vbox(spacing=0)
    adv_card.setLayout(alay)
    alay.addWidget(w.advanced_panel)
    adv_card.setVisible(w.advanced_toggle.isChecked())  # then follows the panel, see sync()
    slay.addWidget(adv_card)
    root.addWidget(strip)

    # Downloads card
    downloads = kit.card(column)
    dlay = kit.vbox(spacing=8)
    downloads.setLayout(dlay)
    dlay.addLayout(kit.hbox(
        kit.title("Downloads", downloads), 10, w.summary_label, None, w.clear_finished_btn,
    ))
    # Ignored: the list takes whatever height is left instead of asking for its default
    # 192 px, so the page only scrolls when it really has to.
    w.queue.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
    w.queue.setContentsMargins(0, 0, 0, 10)  # air between a half-shown row and the banner
    dlay.addWidget(w.queue, 3)
    dlay.addWidget(w.banner)
    dlay.addLayout(kit.hbox(w.log_toggle, None, w.copy_log_btn, w.clear_log_btn))
    dlay.addWidget(w.log_view, 2)
    root.addWidget(downloads, 1)

    state = {"compact": False}

    def owns_parts() -> bool:
        return content.isAncestorOf(w.url_edit)  # False once another variant has taken over

    def sync() -> None:
        """Advanced card follows its panel; with Advanced or Log open the page scrolls
        anyway, so the list keeps three full rows instead of shrinking to one."""
        if not owns_parts():
            return
        adv_card.setVisible(not w.advanced_panel.isHidden())
        if not w.advanced_panel.isHidden() or not w.log_view.isHidden():
            w.queue.setMinimumHeight(_queue_rows_height(w, 3))
        else:
            w.queue.setMinimumHeight(_queue_rows_height(w, 1) + 2 if state["compact"] else 130)

    def apply(tier: int, wide: bool) -> None:
        """Shorter window: smaller question, tighter hero; really short: question tucked
        away. Narrow window: two field rows, slimmer chips and list columns so names,
        paths and titles stay readable."""
        if not owns_parts():
            return
        compact = tier > 0
        state["compact"] = compact
        headline.setVisible(tier < 2)
        hero.setProperty("compact", compact)
        strip.setProperty("narrow", not wide)
        for widget in (hero, headline, strip, w.download_btn, w.preset_hint, *chip_parts):
            _restyle(widget)
        chips.set_gap(8 if wide else 6)  # narrow: all chips still fit one line at 820 px
        chips.invalidate()
        headline.setContentsMargins(0, 0, 0, 12 if tier == 0 else 7)
        root.setContentsMargins(0, 4 if compact else 8, 0, 6)
        root.setSpacing(8 if compact else 10)
        hlay.setContentsMargins(*((24, 10 if tier == 1 else 8, 24, 10) if compact else (28, 16, 28, 18)))
        actions.setContentsMargins(0, 8 if compact else 14, 0, 0)
        w.url_edit.setFixedHeight(_link_box_height(w.url_edit, 2 if compact else 3))
        blay.setContentsMargins(*((10, 5, 10, 5) if compact else (10, 8, 10, 8)))
        dlay.setContentsMargins(*((18, 8, 18, 6) if compact else (18, 12, 18, 10)))
        dlay.setSpacing(6 if compact else 8)
        slay.setSpacing(6 if compact else 10)
        grid.setVerticalSpacing(4 if compact else 6)
        alay.setContentsMargins(EDGE - 1, 10, EDGE - 1, 12)  # the card has a 1 px border
        arrange(wide)
        # "Options" sits centred beside the first line of chips, however they wrap.
        options_label.setFixedHeight(max(widget.sizeHint().height() for widget in chip_parts))
        sync()

    def fit_columns(page_width: int) -> None:
        """Fixed list columns grow from their narrow to their wide widths between 780 and
        1000 px, so the Title column (the one that matters) keeps about 200 px or more."""
        if not owns_parts():
            return
        share = min(1.0, max(0.0, (page_width - 780) / 220))
        for (index, narrow), (_, roomy) in zip(NARROW_COLUMNS, WIDE_COLUMNS):
            width = round(narrow + (roomy - narrow) * share)
            if w.queue.columnWidth(index) != width:
                w.queue.setColumnWidth(index, width)

    apply(0, True)
    fit_columns(MAX_WIDTH)
    _Responsive(content, apply, fit_columns)
    _ShownHidden(content, (w.advanced_panel, w.log_view), sync)
    _BannerFit(content, w.banner)
    _paint_decor(headline, mark, current())


def _paint_decor(headline: QLabel | None, mark: QLabel | None, t: Theme) -> None:
    """Colours that live in text / pixmaps rather than in the stylesheet."""
    if headline is not None:
        headline.setText(HEADLINE.format(accent=_accent_text(t)))
    if mark is not None:
        mark.setPixmap(glyph_pixmap("download", t.color("on_accent"), 15))


def _disabled_download(t: Theme) -> tuple[str, str]:
    """Download pill while there is nothing to download: (background start, label)."""
    if t.dark:  # deep, muted violet with a soft light label
        return t.accent_disabled, mix(t.accent_disabled, t.on_accent, 0.62)
    # Light: pale tonal pill with a violet label, clearly "not yet" but easy to read.
    start = mix(t.window, t.accent, 0.24)
    return start, mix(t.accent_pressed, start, 0.22)


def after_refresh(w: MainWindow, t: Theme) -> None:
    # The arrow matches the label in both states (the default disabled arrow is a pale
    # white that would vanish on the light tonal pill).
    w.download_btn.setIcon(glyph_icon("download", t.color("on_accent"), QColor(_disabled_download(t)[1])))
    w.paste_btn.setIcon(glyph_icon("paste", QColor(_accent_text(t))))
    w.settings_button.setIcon(glyph_icon("settings", t.color("muted")))
    _paint_decor(w.findChild(QLabel, "heroTitle"), w.findChild(QLabel, "spotMark"), t)


def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:
    mag = _magenta(t)
    accent_text = _accent_text(t)
    surface, surface_hover, surface_pressed = _surface(t)
    glow = 0.15 if t.dark else 0.10
    hero_top = mix(t.window, t.accent, glow)
    hero_mid = mix(t.window, mag, glow * 0.45)
    hero_edge = mix(t.window, t.accent, 0.30 if t.dark else 0.22)
    box_border = mix(t.border_strong, t.accent, 0.22)
    grad_h = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {a}, stop:1 {b})"
    # White label ≥ 4.5:1 over the whole violet → magenta run, in both schemes.
    primary = grad_h.format(a=surface, b=mag)
    primary_hover = grad_h.format(a=surface_hover, b=mix(mag, "#ffffff", 0.08))
    primary_pressed = grad_h.format(a=surface_pressed, b=mix(mag, "#000000", 0.15))
    off_start, primary_off_text = _disabled_download(t)
    off_end = mix(off_start, mag, 0.22) if t.dark else mix(t.window, mag, 0.2)
    primary_off = grad_h.format(a=off_start, b=off_end)
    ghost_off_text = mix(t.muted, t.window, 0.45)
    mark = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {a}, stop:1 {b})".format(a=surface, b=mag)
    chip_on_bg = mix(t.card, t.accent, 0.16 if t.dark else 0.08)
    chip_on_border = mix(t.card, t.accent, 0.55)
    chip_off_ring = mix(t.muted, t.card, 0.25)
    menu_border = mix(t.border_strong, t.window, 0.25)
    return f"""
QLabel#spotMark {{ background: {mark}; border-radius: 8px; }}
QPushButton#primary, QPushButton#accentSmall {{ background: {surface}; }}
QPushButton#primary:hover, QPushButton#accentSmall:hover {{ background: {surface_hover}; }}
QPushButton#primary:pressed, QPushButton#accentSmall:pressed {{ background: {surface_pressed}; }}
QLineEdit, QComboBox, QPlainTextEdit {{ selection-background-color: {surface}; }}
QLabel#appTagline {{ color: {t.muted}; }}
QToolButton#menuButton {{
    background: transparent; border: 1px solid {menu_border}; border-radius: 15px;
    color: {t.muted}; padding: 4px 15px 4px 8px;
}}
QToolButton#menuButton:hover {{ background: {t.accent_soft}; border-color: {chip_on_border}; color: {t.text}; }}
QToolButton#menuButton::menu-indicator {{ subcontrol-position: center right; right: 8px; }}

QFrame#hero {{
    border-radius: 28px;
    border: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {hero_edge}, stop:1 {t.window});
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {hero_top}, stop:0.6 {hero_mid}, stop:1 {t.window});
}}
QFrame#hero QLabel {{ background: transparent; }}
QLabel#heroTitle {{
    font-size: 21pt; font-weight: 700; color: {t.text};
    font-family: "Segoe UI Variable Display", "Segoe UI", "Noto Sans", "DejaVu Sans";
}}
QFrame#hero[compact="true"] QLabel#heroTitle {{ font-size: 16pt; }}

QFrame#searchBox {{
    background: {t.card}; border: 1px solid {box_border}; border-radius: 22px;
}}
QFrame#searchBox:hover {{ border-color: {mix(box_border, t.accent, 0.35)}; }}
QFrame#searchBox[focused="true"] {{ border-color: {t.accent}; }}
QFrame#searchBox QPlainTextEdit#links {{
    background: {t.card}; border: 2px solid transparent; border-radius: 14px;
    padding: 0px 4px; font-size: {m.font_size + 1.5}pt;
}}
QFrame#searchBox QPlainTextEdit#links::corner {{ background: transparent; border: none; }}
QFrame#searchBox QPlainTextEdit#links[dragActive="true"] {{
    border: 2px dashed {t.accent}; background: {t.accent_soft};
}}
QFrame#searchBox QScrollBar:horizontal {{ height: 8px; margin: 0px 2px; }}
QFrame#searchBox QScrollBar:vertical {{ width: 8px; margin: 2px 0px; }}
QFrame#searchBox QScrollBar::handle:horizontal {{ margin: 1px 0px; }}
QFrame#searchBox QScrollBar::handle:vertical {{ margin: 0px 1px; }}
QPushButton[role="spotPaste"] {{
    background: {t.accent_soft}; color: {accent_text}; border: none; border-radius: 15px;
    padding: 0px 16px; min-height: 30px; max-height: 30px; font-weight: 600;
}}
QPushButton[role="spotPaste"]:hover {{ background: {mix(t.accent_soft, t.accent, 0.12)}; }}
QPushButton[role="spotPaste"]:pressed {{ background: {mix(t.accent_soft, t.accent, 0.22)}; }}
QFrame#searchBox QPushButton#flat {{ border-radius: 11px; padding: 2px 8px; }}

QPushButton#primary[role="spotlight"] {{
    background: {primary}; border-radius: 24px; padding: 0px 38px; min-height: 48px; max-height: 48px;
    font-size: {m.primary_font_size + 0.5}pt; font-weight: 700;
}}
QPushButton#primary[role="spotlight"]:hover {{ background: {primary_hover}; }}
QPushButton#primary[role="spotlight"]:pressed {{ background: {primary_pressed}; }}
QPushButton#primary[role="spotlight"]:disabled {{ background: {primary_off}; color: {primary_off_text}; }}
QFrame#hero[compact="true"] QPushButton#primary[role="spotlight"] {{
    min-height: 44px; max-height: 44px; border-radius: 22px;
}}
QPushButton#secondary[role="ghost"] {{
    background: transparent; border: 1px solid transparent; border-radius: 20px;
    color: {t.muted}; padding: 0px 18px; min-height: 38px; max-height: 38px;
    font-size: {m.font_size}pt; font-weight: 600;
}}
QPushButton#secondary[role="ghost"]:hover {{ background: {t.accent_soft}; color: {t.danger}; }}
QPushButton#secondary[role="ghost"]:disabled {{ color: {ghost_off_text}; background: transparent; }}

QFrame#strip {{ background: transparent; }}
QFrame#strip QLabel {{ background: transparent; }}
QWidget#pageContent QComboBox, QWidget#pageContent QLineEdit {{ border-radius: 16px; padding-left: 12px; }}
QWidget#pageContent QLineEdit {{ padding-left: 8px; }}
QFrame#strip QComboBox, QFrame#strip QLineEdit {{ background: {t.card}; }}
QFrame#strip QPushButton {{ border-radius: 16px; padding: 6px 15px; }}
QFrame#strip QPushButton#flat {{ padding: 4px 10px; }}
QCheckBox[role="chip"] {{
    background: {t.card}; border: 1px solid {t.border_strong}; border-radius: 14px;
    padding: 5px 12px 5px 9px; spacing: 7px;
}}
QCheckBox[role="chip"]:hover {{ border-color: {chip_on_border}; }}
QCheckBox[role="chip"]:checked {{ background: {chip_on_bg}; border-color: {chip_on_border}; }}
QCheckBox[role="chip"]::indicator {{ width: 14px; height: 14px; border-radius: 7px; }}
QCheckBox[role="chip"]::indicator:unchecked {{ border: 1px solid {chip_off_ring}; }}
QCheckBox[role="chip"]::indicator:unchecked:hover {{ border-color: {t.accent}; }}
QFrame#strip[narrow="true"] QLabel#muted {{ font-size: {m.font_size - 1}pt; }}
QFrame#strip[narrow="true"] QCheckBox[role="chip"] {{ padding: 5px 10px 5px 8px; spacing: 6px; }}
QFrame#chipRule {{ background: {t.border_strong}; border: none; }}
QToolButton#sectionToggle[role="chipToggle"] {{
    background: transparent; border: 1px solid transparent; border-radius: 14px;
    color: {t.muted}; padding: 5px 12px 5px 8px;
}}
QFrame#strip[narrow="true"] QToolButton#sectionToggle[role="chipToggle"] {{ padding: 5px 10px 5px 6px; }}
QToolButton#sectionToggle[role="chipToggle"]:hover {{
    background: {t.accent_soft}; color: {accent_text};
}}
QToolButton#sectionToggle[role="chipToggle"]:checked {{
    background: {chip_on_bg}; border-color: {chip_on_border}; color: {t.text};
}}
QFrame#advCard {{
    background: {t.card}; border: 1px solid {t.border}; border-radius: 18px;
}}
QFrame#advCard QLabel {{ background: transparent; }}

QTreeWidget#queue QHeaderView::section {{ font-size: {m.font_size - 0.5}pt; }}
"""


VARIANT = Variant(
    key="spotlight",
    name="Spotlight",
    description="A search-engine-like hero: one big link box and a pill Download button in a soft violet glow.",
    light=LIGHT,
    dark=DARK,
    metrics=Metrics(
        font_families=("Segoe UI Variable Text", "Segoe UI", "Noto Sans", "DejaVu Sans"),
        font_size=10.0,
        heading_families=("Segoe UI Variable Display", "Segoe UI", "Noto Sans", "DejaVu Sans"),
        app_title_size=11.5,
        card_title_size=11.5,
        heading_weight=600,
        card_radius=18,
        control_radius=10,
        primary_radius=20,
        control_padding=(6, 12),
        button_padding=(6, 15),
        primary_padding=(10, 30),
        primary_font_size=11.5,
        indicator_size=16,
        row_extra=22,
    ),
    build_layout=build,
    extra_qss=extra_qss,
    after_refresh=after_refresh,
)
