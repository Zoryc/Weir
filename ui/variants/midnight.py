"""Midnight: dark-only premium look with a two-pane split.

Deep navy page with soft violet/cyan glows, translucent "glass" panels and an
electric violet-to-cyan gradient on the Download button.

Left pane (~45 % of a wide window): the new-download composer, read top to bottom -
links, preset, options, then the Download button. Advanced options open under the
Download button, so opening them never pushes it out of sight; the page scrolls a
little instead on small screens (no scroll area inside the page).

Right pane: the downloads list (Cancel all and Clear finished in its header, the
Save-to folder at its foot, next to the files it describes) with a slim log drawer
stacked under it. Two deliberate deviations from a literal "list and log side by
side, folder in the composer": at the default size the right pane is only ~580 px
wide and the list needs all of it for its six columns, and the composer has no
height to spare on an 820x620 window with an error showing.

Errors appear in a full-width strip under the header so the message always has room
next to its fix buttons. On narrow windows the composer gives way first (down to the
width its preset dropdown needs) so the list keeps readable titles and status pills.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent, QRadialGradient, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QLayout,
    QLayoutItem,
    QPlainTextEdit,
    QSizePolicy,
    QSpacerItem,
    QTreeWidget,
    QWidget,
)

from .. import kit
from ..icon import glyph_icon
from ..theme import GlyphFile, Metrics, Theme, Variant
from ..widgets import Card

if TYPE_CHECKING:
    from ..main_window import MainWindow

# ---------------------------------------------------------------- palette
# The variant always runs dark, so these fixed colours never meet a light scheme.
PAGE = "#080b14"
VIOLET = "#8b7bff"
VIOLET_DEEP = "#7b4dff"
INDIGO = "#4d5cf0"
CYAN_DEEP = "#0f86bd"  # gradient end: deep enough that white text keeps >= 4.5:1
FIELD = "#0c111e"  # inset inputs
FIELD_BORDER = "#29324d"
FIELD_HOVER = "#3a4568"
LABEL = "#b4bcd6"  # form labels: a step brighter than muted text
DISABLED_TEXT = "#727b99"  # ~4:1 on the glass cards: dimmed but still discoverable

DARK = Theme(
    dark=True,
    window=PAGE,
    card="#111727",
    alt="#151c2e",
    hover="#1b2338",
    border="#1f2840",
    border_strong="#3a4568",  # also the empty list's inbox icon
    text="#e9ecf6",
    muted="#98a2bf",
    accent="#38d3f2",
    accent_hover="#62def7",
    accent_pressed="#1fb6d5",
    accent_disabled="#1a3445",
    accent_soft="#11283a",
    selection="#262a58",
    track="#1d2539",
    success="#3ddc97",
    warning="#ffb648",
    danger="#ff5f78",
    scroll="#3a4568",
    on_accent="#04121f",
)
LIGHT = DARK  # dark-only variant: the light scheme uses the same colours

# ------------------------------------------------------------ split widths
_SPLIT_GAP = 10
_COMPOSER_SHARE = 0.45
_COMPOSER_MIN = 298  # the longest built-in preset name still fits its dropdown whole
_COMPOSER_MAX = 760
_LIST_ROOM = 580  # the list keeps this much width before the composer grows past its minimum


def _split_widths(total: int) -> tuple[int, int]:
    """Composer and list widths for ``total`` px (gap excluded).

    The composer aims for ~45 %, but the list first gets enough room for readable
    titles; on narrow windows the composer shrinks towards its minimum instead.
    """
    left = min(round(total * _COMPOSER_SHARE), total - _LIST_ROOM, _COMPOSER_MAX)
    left = max(_COMPOSER_MIN, left)
    return left, max(0, total - left)


# ----------------------------------------------------------- queue widths
# Fixed queue columns (indices as in MainWindow): Preset, Progress, Speed, ETA, Status.
# The title column stretches into whatever is left.
_FIXED_COLUMNS = (1, 2, 3, 4, 5)
_MIN_WIDTHS = (88, 84, 66, 46, 116)
_IDEAL_WIDTHS = (130, 120, 80, 56, 122)
# Narrow windows: columns give way in this order down to these floors, which still show
# "4.21MiB/s", "00:12", "100%" and the whole "Downloading" pill; the preset name goes last.
_FLOORS = (54, 72, 66, 40, 116)  # (speed and status never shrink)
_SQUEEZE_ORDER = (1, 3, 0)
_TITLE_SQUEEZED = 120  # narrow windows squeeze the other columns until the title has this
_TITLE_MIN = 150  # the title gets this much before the other columns grow
_TITLE_IDEAL = 260
_TITLE_LAST = 40  # below every floor, everything shrinks evenly above this
_PRESET_MAX = 230  # very wide windows: the preset name gets a share of the extra room
_QUEUE_SIDE = 2  # px between the card edge and the list


def _queue_widths(avail: int) -> list[int]:
    """Widths for the fixed queue columns so that nothing ever scrolls sideways."""
    widths = list(_MIN_WIDTHS)
    spare = avail - sum(widths) - _TITLE_MIN
    if spare < 0:
        missing = sum(widths) + _TITLE_SQUEEZED - avail
        for index in _SQUEEZE_ORDER:
            give = max(0, min(missing, widths[index] - _FLOORS[index]))
            widths[index] -= give
            missing -= give
            if missing <= 0:
                break
        budget = avail - _TITLE_LAST
        if sum(widths) > budget:  # transient tiny sizes (mid-relayout): never scroll sideways
            scale = max(0.0, budget) / sum(widths)
            widths = [max(16, int(width * scale)) for width in widths]
        return widths
    spare -= min(spare, _TITLE_IDEAL - _TITLE_MIN)  # titles matter most: they grow first
    grow = [hi - lo for lo, hi in zip(_MIN_WIDTHS, _IDEAL_WIDTHS)]
    k = min(1.0, spare / sum(grow))
    widths = [lo + round(g * k) for lo, g in zip(_MIN_WIDTHS, grow)]
    spare -= sum(widths) - sum(_MIN_WIDTHS)
    if spare > 0:
        widths[0] = min(_PRESET_MAX, widths[0] + round(spare * 0.3))
    return widths


# ----------------------------------------------------------- decorations


class _Backdrop(QWidget):
    """Page background: deep navy with a violet glow top-left and a cyan glow lower right.

    The bottom edge fades back to plain navy so the page meets the status bar seamlessly.
    """

    FADE = 70  # px

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(PAGE))
        span = float(max(rect.width(), rect.height()))
        for cx, cy, radius, rgba in (
            (0.06, -0.18, 0.78, (112, 76, 255, 64)),
            (1.02, 0.86, 0.50, (20, 184, 230, 36)),
        ):
            glow = QRadialGradient(QPointF(rect.width() * cx, rect.height() * cy), span * radius)
            inner = QColor(*rgba)
            outer = QColor(inner)
            outer.setAlpha(0)
            glow.setColorAt(0.0, inner)
            glow.setColorAt(1.0, outer)
            painter.fillRect(rect, glow)
        fade = QLinearGradient(0.0, float(rect.height() - self.FADE), 0.0, float(rect.height()))
        clear = QColor(PAGE)
        clear.setAlpha(0)
        fade.setColorAt(0.0, clear)
        fade.setColorAt(1.0, QColor(PAGE))
        painter.fillRect(QRect(0, rect.height() - self.FADE, rect.width(), self.FADE), fade)
        painter.end()


class _GlowEffect(QGraphicsDropShadowEffect):
    """Soft violet glow under the Download button, switched off while it is disabled."""

    def __init__(self, button: QAbstractButton, parent: QWidget) -> None:
        super().__init__(parent)
        self._button = button
        self.setOffset(0, 5)
        self.setBlurRadius(30)
        self.setColor(QColor(110, 80, 255, 120))

    def draw(self, painter: QPainter) -> None:
        holder = self.parent()
        if self._button.isEnabled() and isinstance(holder, QWidget) and holder.isAncestorOf(self._button):
            super().draw(painter)
        else:
            self.drawSource(painter)


class _SplitLayout(QLayout):
    """Composer and downloads side by side, sized by ``_split_widths``.

    A plain box layout can only share space by fixed ratios; this one lets the list keep
    its room on narrow windows while the composer still reaches ~45 % on wide ones.
    """

    def __init__(self, spacing: int) -> None:
        super().__init__()
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    # QLayout plumbing
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 - Qt API
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt API
        return Qt.Orientation.Horizontal | Qt.Orientation.Vertical

    # sizes
    def _widths(self, width: int) -> list[int]:
        left, right = _split_widths(max(0, width - self.spacing()))
        return [left, right]

    def _heights(self, width: int, minimum: bool) -> int:
        result = 0
        for item, item_width in zip(self._items, self._widths(width)):
            if item.hasHeightForWidth():
                height = item.minimumHeightForWidth(item_width) if minimum else item.heightForWidth(item_width)
            else:
                height = (item.minimumSize() if minimum else item.sizeHint()).height()
            result = max(result, height)
        return result

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt API
        widths = sum(item.minimumSize().width() for item in self._items)
        width = max(widths + self.spacing(), _COMPOSER_MIN + self.spacing())
        return QSize(width, max((item.minimumSize().height() for item in self._items), default=0))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(_COMPOSER_MIN + self.spacing() + _LIST_ROOM, self._heights(1040, minimum=False))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return any(item.hasHeightForWidth() for item in self._items)

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._heights(width, minimum=False)

    def minimumHeightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._heights(width, minimum=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        x = rect.x()
        for item, width in zip(self._items, self._widths(rect.width())):
            item.setGeometry(QRect(x, rect.y(), width, rect.height()))
            x += width + self.spacing()


class _DownloadsCard(Card):
    """Glass card that fits the queue's columns to its width (no sideways scrolling).

    Widths depend only on the card's own width (the vertical scrollbar is always
    reserved; the title column stretches into it while it is hidden), so scrollbars
    coming and going can never feed back into another resize.
    """

    def __init__(self, queue: QTreeWidget, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("glass")
        self._queue = queue

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        queue = self._queue
        if not self.isAncestorOf(queue):
            return
        bar = queue.verticalScrollBar().sizeHint().width()
        avail = self.width() - 2 - 2 * _QUEUE_SIDE - 2 * queue.frameWidth() - bar
        for column, width in zip(_FIXED_COLUMNS, _queue_widths(avail)):
            if queue.columnWidth(column) != width:
                queue.setColumnWidth(column, width)


class _LinksFromStart(QObject):
    """While the link box is not being edited, show the links from their start.

    Links never wrap, and adding one leaves the text scrolled to its far end, which cuts
    off the front of every shorter link ("om/watch?v=…"). Like a browser's address bar,
    the box returns to the start of the lines when it is not focused. Only scroll
    position changes; the text, cursor and focus are untouched.
    """

    def __init__(self, owner: QObject, edit: QPlainTextEdit) -> None:
        super().__init__(owner)
        self._edit = edit
        self._revision = -1
        edit.installEventFilter(self)  # removed automatically when this object dies
        edit.viewport().installEventFilter(self)

    def _home(self) -> None:
        bar = self._edit.horizontalScrollBar()
        if bar.value() != bar.minimum():
            bar.setValue(bar.minimum())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        edit = self._edit
        kind = event.type()
        if kind == QEvent.Type.FocusOut and watched is edit:
            self._home()
        elif kind == QEvent.Type.Paint and watched is edit.viewport():
            revision = edit.document().revision()
            if revision != self._revision:  # links were added or removed
                self._revision = revision
                if not edit.hasFocus():
                    self._home()
        return False


class _Grow(QWidget):
    """Holds a growable part: asks for a modest height and takes spare room via stretch.

    The page sizes itself to the preferred heights of what it holds, so big default
    hints (a text box asks for ~190 px) would make it scroll on small windows.
    """

    def __init__(self, child: QWidget, parent: QWidget, preferred: int) -> None:
        super().__init__(parent)
        self._preferred = preferred
        self.setLayout(kit.vbox(child))

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(hint.width(), max(self.minimumSizeHint().height(), self._preferred))


def _accent_bar(parent: QWidget) -> QFrame:
    bar = kit.panel(parent, "accentBar")
    bar.setFixedSize(4, 18)
    return bar


def _section_title(text: str, parent: QWidget) -> QLabel:
    label = kit.title(text, parent)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return label


def _fixed_spacer() -> QSpacerItem:
    """Horizontal stretch that never asks for extra height (keeps the log bar slim)."""
    return QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def _link_box_height(edit: QPlainTextEdit, lines: int) -> int:
    """Height that shows ``lines`` whole lines even with the sideways scrollbar showing."""
    edit.ensurePolished()
    edit.horizontalScrollBar().ensurePolished()
    text = lines * edit.fontMetrics().lineSpacing() + 2 * edit.document().documentMargin()
    bar = edit.horizontalScrollBar().sizeHint().height()
    return int(text) + 2 * edit.frameWidth() + bar + 2


# ----------------------------------------------------------------- layout


def build(w: MainWindow, content: QWidget) -> None:
    outer = kit.vbox(margins=(0, 0, 0, 0), spacing=0)
    content.setLayout(outer)
    backdrop = _Backdrop(content)
    outer.addWidget(backdrop)

    root = kit.vbox(spacing=8, margins=(10, 8, 10, 10))
    backdrop.setLayout(root)

    # Header: logo · title · tagline … Settings
    w.set_logo_size(26)
    root.addLayout(kit.hbox(
        w.logo_label, 2, w.title_label, 14, w.tagline_label, None, w.settings_button,
        spacing=8,
    ))

    # Errors get the full width so the fix buttons never squeeze the message.
    root.addWidget(w.banner)

    split = _SplitLayout(_SPLIT_GAP)
    split.addWidget(_composer(w, backdrop))
    split.addWidget(_downloads_column(w, backdrop))
    root.addLayout(split, 1)


def _composer(w: MainWindow, parent: QWidget) -> QWidget:
    """Left pane: links, preset, options, then Download; Advanced opens below it."""
    card = kit.card(parent, "glass")
    lay = kit.vbox(spacing=0)
    card.setLayout(lay)

    kit.set_role(w.paste_btn, "mini")
    lay.addLayout(kit.hbox(
        _accent_bar(card), 4, _section_title("New download", card), None,
        w.clear_links_btn, w.paste_btn,
        spacing=4, margins=(14, 11, 12, 6),
    ))

    form = kit.vbox(spacing=0, margins=(14, 0, 14, 0))
    lay.addLayout(form, 1)
    # The link box is the drop zone: it takes all spare height, so the Download button
    # always sits right under the options with no dead band above it.
    w.url_edit.setMinimumHeight(_link_box_height(w.url_edit, 2))
    _LinksFromStart(card, w.url_edit)
    form.addWidget(_Grow(w.url_edit, card, preferred=0), 1)
    form.addSpacing(11)

    w.preset_combo.setMinimumContentsLength(12)
    form.addWidget(kit.field_label("Preset", card))
    form.addSpacing(5)
    form.addWidget(w.preset_combo)
    form.addSpacing(4)
    form.addWidget(w.preset_hint)
    form.addSpacing(11)

    form.addWidget(kit.field_label("Options", card))
    form.addSpacing(5)
    form.addLayout(kit.vbox(w.playlist_check, w.subs_check, w.meta_check, spacing=5))

    glow = QWidget(card)
    glow.setObjectName("primaryGlow")
    glow.setLayout(kit.hbox(w.download_btn))
    glow.setGraphicsEffect(_GlowEffect(w.download_btn, glow))
    lay.addLayout(kit.hbox((glow, 1), margins=(14, 13, 14, 8)))

    lay.addLayout(kit.hbox(kit.divider(card), margins=(14, 0, 14, 0)))
    lay.addLayout(kit.hbox(w.advanced_toggle, None, margins=(12, 2, 14, 3)))
    w.advanced_panel.set_stacked(True)
    lay.addLayout(kit.hbox(w.advanced_panel, margins=(14, 0, 14, 14)))
    return card


def _downloads_column(w: MainWindow, parent: QWidget) -> QWidget:
    """Right pane: the downloads list (and where it saves to), with the log drawer under it."""
    column = QWidget(parent)
    lay = kit.vbox(spacing=_SPLIT_GAP)
    column.setLayout(lay)

    card = _DownloadsCard(w.queue, column)
    dlay = kit.vbox(spacing=0, margins=(1, 11, 1, 1))
    card.setLayout(dlay)
    kit.set_role(w.cancel_btn, "compact")
    dlay.addLayout(kit.hbox(
        _accent_bar(card), 4, _section_title("Downloads", card), None,
        w.clear_finished_btn, w.cancel_btn,
        spacing=6, margins=(15, 0, 13, 0),
    ))
    # Summary under the title (indented to its text); it keeps its line when empty so
    # the list never jumps up or down.
    w.summary_label.setMinimumHeight(w.summary_label.fontMetrics().height())
    dlay.addLayout(kit.hbox(w.summary_label, None, margins=(29, 0, 13, 8)))
    side = _QUEUE_SIDE - 1
    w.queue.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
    dlay.addLayout(kit.hbox(w.queue, margins=(side, 0, side, 0)), 1)
    dlay.addLayout(kit.hbox(kit.divider(card), margins=(side, 0, side, 0)))
    # Where the files go, right under them; the path gets the whole remaining width.
    for button in (w.browse_btn, w.open_folder_btn):
        kit.set_role(button, "slim")
    dlay.addLayout(kit.hbox(
        kit.field_label("Save to", card), 4, (w.folder_edit, 1), w.browse_btn, w.open_folder_btn,
        spacing=6, margins=(15, 10, 13, 11),
    ))
    lay.addWidget(card, 3)

    log = kit.card(column, "glass")
    llay = kit.vbox(spacing=8, margins=(12, 7, 12, 8))
    # Collapsed, the card is just its header bar; open, it shares the column with the list.
    llay.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
    log.setLayout(llay)
    caption = kit.muted("yt-dlp's own output", log)
    caption.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    head = kit.hbox(w.log_toggle, 2, caption, spacing=6)
    head.addSpacerItem(_fixed_spacer())
    head.addWidget(w.copy_log_btn)
    head.addWidget(w.clear_log_btn)
    llay.addLayout(head)
    w.log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
    llay.addWidget(w.log_view, 2)
    lay.addWidget(log, 2)
    return column


# ------------------------------------------------------------------ style


def _after_refresh(w: MainWindow, t: Theme) -> None:  # noqa: ARG001
    # The gradient button is dark in the middle: keep its arrow white, not on_accent navy.
    w.download_btn.setIcon(glyph_icon("download", QColor("#ffffff"), QColor(255, 255, 255, 120)))


def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:  # noqa: ARG001
    heading = "font-family: " + ", ".join(f'"{f}"' for f in m.heading_families) + ";"
    grad = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {0}, stop:0.55 {1}, stop:1 {2})"
    ghost = "rgba(255, 255, 255, 9)"
    ghost_border = "rgba(150, 168, 255, 42)"
    violet_hover = "rgba(139, 123, 255, 34)"
    violet_hover_border = "rgba(139, 123, 255, 120)"
    hairline = "rgba(140, 160, 255, 26)"
    bar = m.scrollbar_width
    return f"""
QWidget#pageContent, QScrollArea#page {{ background: {PAGE}; }}

QFrame#glass {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(27, 35, 60, 225), stop:1 rgba(15, 20, 36, 230));
    border: 1px solid rgba(130, 150, 255, 30);
    border-top-color: rgba(170, 185, 255, 52);
    border-radius: {m.card_radius}px;
}}
QFrame#glass QLabel {{ background: transparent; }}
QFrame#accentBar {{
    border: none; border-radius: 2px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {VIOLET}, stop:1 {t.accent});
}}
QFrame#divider {{ background: {hairline}; }}

QLabel#appTitle {{ color: {t.text}; {heading} }}
QLabel#appTagline {{ color: {t.muted}; padding-top: 2px; }}
QLabel#cardTitle {{ color: {t.text}; {heading} }}
QLabel#fieldLabel {{ color: {LABEL}; font-size: {m.font_size - 0.5}pt; }}
QLabel#muted {{ color: {t.muted}; }}

QPushButton {{ background: {ghost}; color: {t.text}; border: 1px solid {ghost_border}; }}
QPushButton:hover {{ background: {violet_hover}; border-color: {violet_hover_border}; }}
QPushButton:pressed {{ background: rgba(139, 123, 255, 20); }}
QPushButton:focus {{ border-color: {VIOLET}; }}
QPushButton:disabled {{
    color: {DISABLED_TEXT}; background: rgba(255, 255, 255, 5); border-color: rgba(150, 168, 255, 26);
}}
QPushButton[role="mini"] {{ padding: 4px 12px; min-height: 18px; }}
QPushButton[role="slim"] {{ padding-left: 10px; padding-right: 10px; }}

QPushButton#primary {{
    background: {grad.format(VIOLET_DEEP, INDIGO, CYAN_DEEP)};
    color: #ffffff; border: 1px solid rgba(255, 255, 255, 34); border-radius: {m.primary_radius}px;
    font-weight: 600; min-height: 24px;
}}
QPushButton#primary:hover {{
    background: {grad.format("#8454ff", "#5563f6", "#108bc4")}; border-color: rgba(255, 255, 255, 70);
}}
QPushButton#primary:pressed {{ background: {grad.format("#6a3ff0", "#3f4fdc", "#0b74a6")}; }}
QPushButton#primary:focus {{ border: 1px solid rgba(255, 255, 255, 150); }}
QPushButton#primary:disabled {{
    background: {grad.format("#2a2552", "#222a4f", "#163246")};
    color: #8a93b3; border: 1px solid rgba(150, 168, 255, 26);
}}
QPushButton#secondary {{ background: transparent; color: {t.text}; }}
QPushButton#secondary[role="compact"] {{
    padding: 4px 12px; min-height: 18px; font-size: {m.font_size}pt; border-radius: {m.control_radius}px;
}}
QPushButton#secondary:hover {{ background: rgba(255, 95, 120, 26); border-color: rgba(255, 95, 120, 120); }}
QPushButton#secondary:disabled {{
    color: {DISABLED_TEXT}; background: transparent; border-color: rgba(150, 168, 255, 26);
}}
QPushButton#flat {{ background: transparent; border: none; color: {t.muted}; }}
QPushButton#flat:hover {{ color: {t.accent}; background: rgba(56, 211, 242, 22); }}
QPushButton#flat:disabled {{ color: {DISABLED_TEXT}; background: transparent; }}
QPushButton#accentSmall {{ background: {t.accent}; color: {t.on_accent}; border: none; }}
QPushButton#accentSmall:hover {{ background: {t.accent_hover}; }}

QToolButton#menuButton {{
    background: {ghost}; color: {t.text}; border: 1px solid {ghost_border};
    border-radius: {m.control_radius}px;
}}
QToolButton#menuButton:hover {{ background: {violet_hover}; border-color: {violet_hover_border}; }}
QToolButton#sectionToggle {{ color: {t.muted}; }}
QToolButton#sectionToggle:hover {{ color: {t.accent}; }}
QToolButton#sectionToggle:checked {{ color: {t.text}; }}

QLineEdit, QComboBox {{ background: {FIELD}; border: 1px solid {FIELD_BORDER}; }}
QLineEdit:hover, QComboBox:hover {{ border-color: {FIELD_HOVER}; }}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border-color: {VIOLET}; }}
QLineEdit:disabled, QComboBox:disabled {{ background: #0e1322; color: {DISABLED_TEXT}; }}
QComboBox {{ padding-left: 7px; padding-right: 2px; }}
QComboBox::drop-down {{ width: 22px; }}
QComboBox QAbstractItemView {{ background: #121829; border: 1px solid {FIELD_BORDER}; }}
QPlainTextEdit {{ background: {FIELD}; border: 1px solid {FIELD_BORDER}; border-radius: {m.control_radius + 3}px; }}
QPlainTextEdit:focus {{ border-color: {VIOLET}; }}
QPlainTextEdit#links {{ padding: 6px 6px 4px 6px; }}
QPlainTextEdit#links[dragActive="true"] {{ border: 2px dashed {t.accent}; background: {t.accent_soft}; }}
QPlainTextEdit#log {{ background: {FIELD}; border: 1px solid {t.border}; color: #b7c0db; }}
QAbstractScrollArea::corner {{ background: transparent; border: none; }}

QCheckBox {{ spacing: 9px; }}
QCheckBox::indicator {{ background: {FIELD}; border: 1px solid #3a4568; border-radius: 5px; }}
QCheckBox::indicator:hover {{ border-color: {VIOLET}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
QCheckBox::indicator:checked:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}

QTreeWidget#queue {{ background: transparent; border: none; font-size: 9pt; }}
QTreeWidget#queue::item {{ border-bottom: 1px solid rgba(140, 160, 255, 20); padding: 0 1px; }}
QTreeWidget#queue::item:hover {{ background: rgba(139, 123, 255, 16); }}
QTreeWidget#queue::item:selected {{ background: {t.selection}; color: {t.text}; }}
QTreeWidget#queue QHeaderView::section {{
    background: transparent; color: #8790ad; font-size: {m.font_size - 1}pt; font-weight: 600;
    border: none; border-bottom: 1px solid rgba(140, 160, 255, 34); padding: 6px 6px 6px 8px;
}}

QProgressBar {{ background: {t.track}; }}
QProgressBar::chunk {{ background: {grad.format(VIOLET, "#5b6cf5", t.accent)}; }}

QFrame#errorBanner {{
    background: rgba(255, 95, 120, 26); border: 1px solid rgba(255, 95, 120, 110);
    border-radius: 12px;
}}

QScrollBar:vertical {{ width: {bar}px; margin: 2px; }}
QTreeWidget#queue QScrollBar:vertical {{ width: {bar - 3}px; margin: 2px 1px; }}
QScrollBar:horizontal {{ height: {bar}px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.scroll}; border-radius: 3px; margin: 0 1px; min-height: 32px; }}
QScrollBar::handle:horizontal {{ background: {t.scroll}; border-radius: 3px; margin: 1px 0; min-width: 32px; }}
QScrollBar::handle:hover {{ background: #55618a; }}
QMenu {{ background: #111727; border: 1px solid {FIELD_BORDER}; }}
QToolTip {{ background: #141b2e; color: {t.text}; border: 1px solid {FIELD_BORDER}; }}
QStatusBar {{ background: {PAGE}; }}
"""


VARIANT = Variant(
    key="midnight",
    name="Midnight",
    description="Dark-only premium split view: glass panels on deep navy with a violet-to-cyan Download button.",
    light=LIGHT,
    dark=DARK,
    metrics=Metrics(
        font_families=("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"),
        font_size=9.5,
        heading_families=("Segoe UI Variable Display", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"),
        mono_families=("Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", "Noto Sans Mono"),
        app_title_size=13.5,
        card_title_size=11.5,
        heading_weight=600,
        card_radius=16,
        control_radius=9,
        primary_radius=12,
        control_padding=(7, 10),
        button_padding=(6, 14),
        primary_padding=(10, 16),
        primary_font_size=11.5,
        indicator_size=17,
        scrollbar_width=12,
        row_extra=24,
    ),
    build_layout=build,
    extra_qss=extra_qss,
    after_refresh=_after_refresh,
    scheme="dark",
)
