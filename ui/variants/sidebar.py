"""Sidebar: a two-pane app layout in the style of modern productivity apps.

A tinted left sidebar holds the app identity and every "how" choice (preset, folder,
options, advanced settings); Settings is pinned to its bottom edge. The right side is one
large rounded panel inset into the window: the link box with Paste / Download on top,
the downloads list and the log below. Indigo-violet accent. The error banner spans the
whole window width under both panes, so its text stays readable in a small window.

Everything in the sidebar lines up on one left edge: fields, section labels, checkbox
boxes, the Advanced chevron and the Settings icon. Clickable rows (checkboxes, toggles,
Settings) draw their hover background a few pixels outside that edge, like nav rows.

The sidebar width, its section spacing (roomy, tight or compact), the main panel's
padding, the link box height and the queue column widths adapt to the window size, so
the layout stays comfortable from the minimum window size up to full screen. Only the
middle of the sidebar scrolls (when Advanced options do not fit): soft fades show that
more is above or below, and opening Advanced options scrolls them into view.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPaintEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from .. import kit
from .. import theme as theme_state
from ..icon import glyph_icon, glyph_pixmap
from ..theme import GlyphFile, Metrics, Theme, Variant, mix

if TYPE_CHECKING:
    from ..main_window import MainWindow

LIGHT = Theme(
    dark=False,
    window="#f3f3f8",
    card="#ffffff",
    alt="#f7f7fb",
    hover="#ebebf3",
    border="#e3e3ec",
    border_strong="#cfcfdc",
    text="#1b1a2e",
    muted="#6a697e",
    accent="#5b4ee6",
    accent_hover="#4f42da",
    accent_pressed="#4336c2",
    accent_disabled="#c4bff3",
    accent_soft="#efedfd",
    selection="#e6e3fc",
    track="#e6e5f0",
    success="#11804e",
    warning="#9a5f00",
    danger="#cf3434",
    scroll="#c8c7d6",
)

DARK = Theme(
    dark=True,
    window="#131318",
    card="#1b1b22",
    alt="#212129",
    hover="#272731",
    border="#2a2a35",
    border_strong="#3c3c4b",
    text="#ececf3",
    muted="#9d9cb0",
    accent="#8075ff",
    accent_hover="#8d83ff",
    accent_pressed="#6a5ef0",
    accent_disabled="#36325c",
    accent_soft="#262446",
    selection="#2e2b55",
    track="#2d2d39",
    success="#4fca8a",
    warning="#f0b452",
    danger="#ff6b6b",
    scroll="#45455c",
)

METRICS = Metrics(
    font_families=("Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"),
    font_size=10.0,
    heading_families=("Segoe UI Variable Display", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"),
    app_title_size=12.5,
    card_title_size=12.0,
    heading_weight=600,
    card_radius=12,
    control_radius=8,
    primary_radius=10,
    control_padding=(6, 9),
    button_padding=(6, 13),
    primary_padding=(11, 26),
    primary_font_size=11.0,
    indicator_size=16,
    scrollbar_width=10,
    row_extra=24,
)

# Window and sidebar sizing. The sidebar is a share of the window width, never narrower
# than its own content (the preset combo shows the longest preset name in full).
NARROW_WINDOW = 900  # below this window width: tighter gaps and a minimum-width sidebar
SIDEBAR_SHARE = 0.318
SIDEBAR_MIN = 292
SIDEBAR_MAX = 336
SIDE_PAD = 16  # field edge inside the sidebar (wide window)
SIDE_PAD_NARROW = 14
NAV_INSET = 6  # clickable rows draw their hover background this far outside the field edge
SIDE_SCROLLBAR = 9  # slim sidebar scrollbar in the right padding; its handle keeps 5px off the panel
PRESET_TEXT_MAX = 240  # px; a very long custom preset name may not widen the sidebar further
PRESET_TEXT_MIN = 160
# Qt subtracts the drop-down from the text area *and* the right padding, so the sidebar's
# combos use almost no right padding: icon + gap, padding, drop-down, border, a little air.
COMBO_PAD_RIGHT, COMBO_DROP = 4, 24
PRESET_CHROME = 16 + 4 + 9 + COMBO_PAD_RIGHT + COMBO_DROP + 2 + 3
FADE_BOTTOM = 30  # soft fade over the bottom of the sidebar while more content is below
FADE_TOP = 16  # and a shorter one at the top while content is scrolled away above
BODY_BOTTOM = 4  # the scrolling part ends this far under its last row
PANEL_GAP = 8  # the main panel floats this far from the window edges
PANEL_GAP_NARROW = 6
SETTINGS_ICON_WIDTH = 22  # the 16px settings glyph plus 6px of air before its text

# Sidebar spacing by level (roomy, tight, compact): the roomiest level whose contents down
# to the Advanced options toggle fit the sidebar height is used.
ROOMY, TIGHT, COMPACT = 0, 1, 2
Sizes = tuple[int, int, int]
GAP_TOP: Sizes = (18, 10, 4)
GAP_TAGLINE: Sizes = (6, 6, 2)
GAP_IDENTITY: Sizes = (24, 12, 7)
GAP_FIELD: Sizes = (6, 6, 4)  # between a section's label, its field and its hint/buttons
GAP_SECTION: Sizes = (20, 12, 7)
GAP_OPTIONS: Sizes = (4, 4, 2)
GAP_BEFORE_ADVANCED: Sizes = (14, 8, 4)
GAP_DIVIDER: Sizes = (6, 6, 4)
LOGO: Sizes = (30, 30, 24)
FOOTER: tuple[tuple[int, int], ...] = ((6, 10), (6, 10), (4, 3))  # (spacing, bottom margin)

# Opening Advanced options scrolls the sidebar so the options are in view, preferably
# with a whole section (or the toggle itself) at the top.
REVEAL_MARGIN = 14  # px kept free under the panel / above the section at the top
REVEAL_WINDOW_MS = 400  # the scroll follows layout changes this long after opening
REVEAL_ANIMATION_MS = 140

# Main panel padding: the queue sits (padding - 10)px inside the panel, so its title text
# (drawn 10px inside the cell) lines up with everything else.
PANEL_PAD = 24
PANEL_PADS = ((640, 18), (540, 14))  # (panel width below which, padding): small windows

# Queue columns. 0 = Title (stretches), 1 Preset, 2 Progress, 3 Speed, 4 ETA, 5 Status.
# BASE: comfortable widths. Wider queues share the extra room (SHARE, up to MAXIMUM, Title
# gets TITLE_SHARE); narrower ones give up room step by step in SHRINK order, so the
# progress bar, the speed and the status pill stay readable as long as possible.
BASE: dict[int, int] = {0: 170, 1: 64, 2: 92, 3: 70, 4: 48, 5: 120}
MAXIMUM: dict[int, int] = {1: 236, 2: 170, 3: 92, 4: 64, 5: 138}
SHARE: dict[int, float] = {1: 0.6, 2: 0.3, 3: 0.05, 4: 0.025, 5: 0.025}
TITLE_SHARE = 0.45
SHRINK: tuple[tuple[tuple[int, int], ...], ...] = (
    ((1, 50),),  # the preset name is the least useful column in a small window; header stays whole
    ((0, 150),),
    ((2, 88), (3, 70), (4, 44), (5, 118)),  # 34px bar, "4.21MiB/s", "00:12", "Downloading"
    ((0, 110),),
    ((2, 72), (3, 58)),  # the minimum window: a short bar, the speed may elide
)  # anything still missing comes out of Title
LINKS_MIN, LINKS_MAX = 92, 124  # link box height range
HINT_MIN_PANEL = 560  # below this panel width the action-row hint is left out


# ---------------------------------------------------------------- adaptive helpers

def _relayout(widget: QWidget) -> None:
    """Re-run a widget's layout when its geometry went stale (seen after big size jumps)."""
    lay = widget.layout()
    if lay is not None and lay.geometry() != widget.contentsRect():
        lay.invalidate()
        lay.activate()


class _SideBody(QWidget):
    """Scrolling sidebar contents; tells the shell when its size needs change."""

    def __init__(self, shell: _Shell) -> None:
        super().__init__()
        self._shell = shell

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        shell = getattr(self, "_shell", None)
        if event.type() == QEvent.Type.LayoutRequest and shell is not None:
            shell.sync_sidebar()
        return handled


class _Sidebar(QFrame):
    """The sidebar frame; re-checks its spacing when its height changes."""

    def __init__(self, shell: _Shell) -> None:
        super().__init__(shell)
        self.setObjectName("sidebar")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._shell = shell

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest:
            _relayout(self)
        return handled

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        _relayout(self)  # the pinned footer must follow the new width in the same pass
        if event.oldSize().height() != event.size().height():
            self._shell.sync_sidebar()
        self._shell.sync_fades()


class _Fade(QWidget):
    """Soft fade over one edge of the sidebar's scrolling part: there is more that way."""

    def __init__(self, parent: QWidget, top: bool) -> None:
        super().__init__(parent)
        self._top = top
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, event: QPaintEvent) -> None:
        window = QColor(theme_state.current().window)
        clear = QColor(window)
        clear.setAlpha(0)
        gradient = QLinearGradient(QPointF(0, 0), QPointF(0, self.height()))
        gradient.setColorAt(0.0, window if self._top else clear)
        gradient.setColorAt(1.0, clear if self._top else window)
        painter = QPainter(self)
        painter.fillRect(self.rect(), gradient)
        painter.end()


class _Panel(QFrame):
    """The main panel: narrower padding in a small window, link box height by room."""

    def __init__(self, parent: QWidget, links: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("mainPanel")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._links = links
        # (layout, left, top, right, bottom) with left/right relative to the panel padding
        self.padded: list[tuple[QBoxLayout, int, int, int, int]] = []
        self.queue_box: QBoxLayout | None = None
        self._pad = -1

    def apply_padding(self) -> None:
        pad = PANEL_PAD
        for below, narrower in PANEL_PADS:
            if self.width() < below:
                pad = narrower
        if pad == self._pad:
            return
        self._pad = pad
        for box, left, top, right, bottom in self.padded:
            box.setContentsMargins(pad + left, top, pad + right, bottom)
        if self.queue_box is not None:
            inset = pad - 10
            self.queue_box.setContentsMargins(inset, 0, inset, 0)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.apply_padding()
        if self.isAncestorOf(self._links):
            room = LINKS_MIN + (event.size().height() - 540) // 2
            height = max(LINKS_MIN, min(LINKS_MAX, room))
            if self._links.maximumHeight() != height:
                self._links.setMaximumHeight(height)


class _Shell(QWidget):
    """Root of the layout: keeps the sidebar in proportion to the window.

    It sizes the sidebar (width, spacing level, preset combo width), keeps the fades and
    the scrollbar padding in step with the sidebar's scrolling part, and scrolls opened
    Advanced options into view. It also reports its real minimum height (the error
    banner wraps its text, which Qt's minimum size ignores) and no height-for-width, so
    the page only scrolls when the minimum content really does not fit.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("shell")
        self.frame: QVBoxLayout | None = None
        self.banner_box: QBoxLayout | None = None
        self.banner: QWidget | None = None
        self.hint: QLabel | None = None
        self.sidebar: QFrame | None = None
        self.scroll: QScrollArea | None = None
        self.body: _SideBody | None = None
        self.body_lay: QVBoxLayout | None = None
        self.footer: QBoxLayout | None = None
        self.footer_rule: QWidget | None = None
        self.identity_row: QLayout | None = None
        self.title: QLabel | None = None
        self.combo: QComboBox | None = None
        self.settings: QWidget | None = None
        self.advanced_toggle: QWidget | None = None
        self.advanced_panel: QWidget | None = None
        self.snaps: list[QWidget] = []  # section starts the reveal likes to put at the top
        self.set_logo: Callable[[int], None] | None = None
        self.fade_top: _Fade | None = None
        self.fade_bottom: _Fade | None = None
        self.gaps: list[tuple[QSpacerItem, Sizes]] = []
        self.preset_width = 0
        self._level = ROOMY
        self._narrow: bool | None = None
        self._scrolling = False
        self._advanced_open: bool | None = None  # None until the first sync after building
        self._revealing = False
        self._animation: QPropertyAnimation | None = None

    # -- width -----------------------------------------------------------------------

    def _pad(self) -> int:
        return SIDE_PAD_NARROW if self.width() < NARROW_WINDOW else SIDE_PAD

    def _apply_margins(self) -> None:
        """Window gaps and sidebar padding for the current window width and scrollbar."""
        if self.body_lay is None or self.frame is None:
            return
        narrow = self.width() < NARROW_WINDOW
        if narrow != self._narrow:
            self._narrow = narrow
            gap = PANEL_GAP_NARROW if narrow else PANEL_GAP
            self.frame.setContentsMargins(0, gap, gap, gap)
            self.frame.setSpacing(gap)
            if self.banner_box is not None:
                self.banner_box.setContentsMargins(gap, 0, 0, 0)
        side = self._pad() - NAV_INSET
        # While the scrollbar shows it takes the right padding, so the contents keep their width.
        right = max(0, side - SIDE_SCROLLBAR) if self._scrolling else side
        margins = self.body_lay.contentsMargins()
        if (margins.left(), margins.right()) != (side, right):
            self.body_lay.setContentsMargins(side, margins.top(), right, margins.bottom())
        if self.footer is not None:
            fm = self.footer.contentsMargins()
            if fm.left() != side:
                self.footer.setContentsMargins(side, fm.top(), side, fm.bottom())

    def _sync_preset_width(self) -> None:
        """The preset combo shows the longest preset name; presets can be added or deleted."""
        combo = self.combo
        if combo is None or combo.count() == 0:
            return
        width = _preset_width(combo, (combo.itemText(i) for i in range(combo.count())))
        self.preset_width = width
        if combo.minimumWidth() != width:
            combo.setMinimumWidth(width)

    def sync_sidebar(self) -> None:
        if self.sidebar is None or self.scroll is None or self.body is None or self.body_lay is None:
            return
        self._track_advanced()
        self._apply_margins()
        self._sync_preset_width()
        if self.width() < NARROW_WINDOW:
            base = SIDEBAR_MIN
        else:
            base = max(SIDEBAR_MIN, min(SIDEBAR_MAX, round(self.width() * SIDEBAR_SHARE)))
        lay = self.body_lay
        margins = lay.contentsMargins()
        content = max(lay.minimumSize().width() - margins.left() - margins.right(),
                      self.preset_width + 2 * NAV_INSET)
        side = self._pad() - NAV_INSET
        width = max(base, content + 2 * side)  # always the no-scrollbar width: nothing jumps
        if self.sidebar.width() != width or self.sidebar.minimumWidth() != width:
            self.sidebar.setFixedWidth(width)
        self._sync_spacing()
        if self.hint is not None:  # decorative; only where it fits on one or two lines
            gap = PANEL_GAP_NARROW if self._narrow else PANEL_GAP
            show = self.width() - width - gap >= HINT_MIN_PANEL
            if self.hint.isVisibleTo(self) != show:
                self.hint.setVisible(show)

    # -- height ----------------------------------------------------------------------

    def _variable_height(self, level: int) -> int:
        """Height of everything the spacing level changes: the gaps and the logo row."""
        title = self.title.sizeHint().height() if self.title is not None else 0
        return sum(sizes[level] for _, sizes in self.gaps) + max(LOGO[level], title)

    def _footer_height(self, level: int) -> int:
        settings = self.settings.sizeHint().height() if self.settings is not None else 0
        spacing, bottom = FOOTER[level]
        return 1 + spacing + settings + bottom

    def _sync_spacing(self) -> None:
        """Use the roomiest spacing whose sidebar fits down to the Advanced options toggle.

        Opening Advanced options (which scrolls anyway) does not change the spacing, so
        the sections above it never jump. Everything is measured from the last layout
        pass, so the decision does not depend on which level is active right now.
        """
        body, toggle, sidebar = self.body, self.advanced_toggle, self.sidebar
        if body is None or toggle is None or sidebar is None or self.identity_row is None or not self.gaps:
            return
        if not body.isAncestorOf(toggle) or sidebar.height() <= 0:
            return
        bottom = toggle.mapTo(body, QPoint(0, toggle.height())).y()
        current = sum(item.geometry().height() for item, _ in self.gaps) + self.identity_row.geometry().height()
        fixed = bottom - current + BODY_BOTTOM
        level = COMPACT
        for candidate in (ROOMY, TIGHT):
            if fixed + self._variable_height(candidate) <= sidebar.height() - self._footer_height(candidate):
                level = candidate
                break
        if level != self._level:
            self._apply_level(level)

    def _apply_level(self, level: int) -> None:
        self._level = level
        for item, sizes in self.gaps:
            if item.sizeHint().height() != sizes[level]:
                item.changeSize(0, sizes[level], QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        if self.set_logo is not None:
            self.set_logo(LOGO[level])
        if self.footer is not None:
            spacing, bottom = FOOTER[level]
            fm = self.footer.contentsMargins()
            self.footer.setSpacing(spacing)
            self.footer.setContentsMargins(fm.left(), fm.top(), fm.right(), bottom)
        if self.body_lay is not None:
            self.body_lay.invalidate()

    # -- scrolling part --------------------------------------------------------------

    def on_scroll_range(self, _minimum: int, maximum: int) -> None:
        scrolling = maximum > 0
        if scrolling != self._scrolling:
            self._scrolling = scrolling
            self._apply_margins()
        if self._revealing:
            self._reveal_advanced()
        self.sync_fades()

    def sync_fades(self, *_args: object) -> None:
        """Fades over the scrolling part's edges while there is more content that way."""
        scroll, sidebar = self.scroll, self.sidebar
        if scroll is None or sidebar is None or self.fade_top is None or self.fade_bottom is None:
            return
        bar = scroll.verticalScrollBar()
        width = max(0, scroll.width() - (SIDE_SCROLLBAR if bar.maximum() > 0 else 0))  # keep the bar clear
        origin = scroll.mapTo(sidebar, QPoint(0, 0))
        self.fade_top.setGeometry(origin.x(), origin.y(), width, FADE_TOP)
        self.fade_bottom.setGeometry(origin.x(), origin.y() + scroll.height() - FADE_BOTTOM, width, FADE_BOTTOM)
        for fade, show in ((self.fade_top, bar.value() > 0),
                           (self.fade_bottom, bar.maximum() - bar.value() > BODY_BOTTOM + 2)):
            if show:
                fade.raise_()
            if fade.isVisibleTo(self) != show:
                fade.setVisible(show)

    def _track_advanced(self) -> None:
        """Notice the user opening Advanced options (not a restored or rebuilt open state)."""
        panel, body = self.advanced_panel, self.body
        if panel is None or body is None:
            return
        is_open = panel.isVisibleTo(body)
        if self._advanced_open is not None and is_open and not self._advanced_open and self.isVisible():
            self._revealing = True
            QTimer.singleShot(REVEAL_WINDOW_MS, self, self._end_reveal)
        self._advanced_open = is_open

    def _end_reveal(self) -> None:
        self._revealing = False

    def _reveal_advanced(self) -> None:
        """Scroll the sidebar so the opened Advanced options are in view.

        Shows the whole panel when it fits, starting at a section boundary where
        possible; otherwise puts the Advanced toggle at the top with the first fields
        under it.
        """
        scroll, body, panel, toggle = self.scroll, self.body, self.advanced_panel, self.advanced_toggle
        if scroll is None or body is None or panel is None or toggle is None:
            return
        bar = scroll.verticalScrollBar()
        view = scroll.viewport().height()
        panel_bottom = panel.mapTo(body, QPoint(0, panel.height())).y()
        toggle_top = toggle.mapTo(body, QPoint(0, 0)).y() - REVEAL_MARGIN
        need = panel_bottom + REVEAL_MARGIN - view
        if need <= bar.value():
            return
        starts = [s.mapTo(body, QPoint(0, 0)).y() - REVEAL_MARGIN for s in self.snaps]
        starts = [y for y in starts if need <= y <= toggle_top]
        target = min(min(starts, default=toggle_top), bar.maximum())
        if target <= bar.value():
            return
        if self._animation is None:
            self._animation = QPropertyAnimation(bar, b"value", self)
            self._animation.setDuration(REVEAL_ANIMATION_MS)
            self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.stop()
        self._animation.setStartValue(bar.value())
        self._animation.setEndValue(target)
        self._animation.start()

    # -- window ----------------------------------------------------------------------

    def hasHeightForWidth(self) -> bool:
        return False

    def minimumSizeHint(self) -> QSize:
        size = super().minimumSizeHint()
        banner = self.banner
        if banner is not None and banner.isVisibleTo(self):
            hfw = banner.heightForWidth(max(200, self.width() - 2 * PANEL_GAP))
            size.setHeight(size.height() + max(0, hfw - banner.minimumSizeHint().height()))
        return size

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.sync_sidebar()
        banner_shown = self.banner is not None and self.banner.isVisibleTo(self)
        if event.oldSize().width() != event.size().width() and banner_shown:
            self.updateGeometry()  # the banner's wrapped text needs a different height now


class _ColumnFitter(QObject):
    """Fits the queue's fixed columns to the space the Title column leaves over."""

    def __init__(self, shell: QWidget, queue: QTreeWidget) -> None:
        super().__init__(shell)
        self._shell = shell
        self._queue = queue
        queue.viewport().installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and self._shell.isAncestorOf(self._queue):
            fit_columns(self._queue, self._queue.viewport().width())
        return False


def column_widths(width: int) -> dict[int, int]:
    """Widths of the non-Title columns for a queue viewport ``width`` px wide."""
    widths = dict(BASE)
    extra = width - sum(BASE.values())
    if extra >= 0:
        others = extra * (1.0 - TITLE_SHARE)
        for column in SHARE:
            widths[column] = int(BASE[column] + min(MAXIMUM[column] - BASE[column], others * SHARE[column]))
    else:
        missing = -extra
        for step in SHRINK:
            for column, low in step:
                give = max(0, min(missing, widths[column] - low))
                widths[column] -= give
                missing -= give
    return {column: widths[column] for column in SHARE}


def fit_columns(queue: QTreeWidget, width: int) -> None:
    header = queue.header()
    for column, target in column_widths(width).items():
        if header.sectionSize(column) != target:
            queue.setColumnWidth(column, target)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)


# ---------------------------------------------------------------------- layout

def _side_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text.upper(), parent)
    label.setObjectName("sideLabel")
    font = label.font()
    font.setLetterSpacing(font.SpacingType.PercentageSpacing, 108)
    label.setFont(font)
    return label


def _inset(*items: kit.Item, spacing: int = 6) -> QVBoxLayout:
    """Non-clickable sidebar content sits NAV_INSET inside the nav rows' edge."""
    return kit.vbox(*items, spacing=spacing, margins=(NAV_INSET, 0, NAV_INSET, 0))


def _gap(shell: _Shell, lay: QBoxLayout, sizes: Sizes) -> None:
    item = QSpacerItem(0, sizes[ROOMY], QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
    lay.addSpacerItem(item)
    shell.gaps.append((item, sizes))


def _section(shell: _Shell, label: QWidget, *items: QWidget | QLayout) -> QVBoxLayout:
    """A sidebar section: its label, then its fields, with level-dependent gaps between."""
    box = _inset(label, spacing=0)
    for item in items:
        _gap(shell, box, GAP_FIELD)
        if isinstance(item, QLayout):
            box.addLayout(item)
        else:
            box.addWidget(item)
    return box


def _preset_width(combo: QComboBox, names: Iterable[str]) -> int:
    """Combo width that shows the longest preset name in full (capped for custom names)."""
    fm = combo.fontMetrics()
    longest = max((fm.horizontalAdvance(name) for name in names), default=0)
    return min(max(longest, PRESET_TEXT_MIN), PRESET_TEXT_MAX) + PRESET_CHROME


def build(w: MainWindow, content: QWidget) -> None:
    outer = kit.vbox(margins=(0, 0, 0, 0), spacing=0)
    content.setLayout(outer)
    shell = _Shell(content)
    outer.addWidget(shell)
    frame = QVBoxLayout(shell)
    frame.setContentsMargins(0, PANEL_GAP, PANEL_GAP, PANEL_GAP)
    frame.setSpacing(PANEL_GAP)
    root = QHBoxLayout()
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    frame.addLayout(root, 1)

    # ----------------------------------------------------------- sidebar
    sidebar = _Sidebar(shell)
    side_lay = kit.vbox(margins=(0, 0, 0, 0), spacing=0)
    sidebar.setLayout(side_lay)
    scroll = QScrollArea(sidebar)
    scroll.setObjectName("sideScroll")
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = _SideBody(shell)
    body.setObjectName("sideBody")
    scroll.setWidget(body)
    scroll.viewport().setAutoFillBackground(False)
    scroll.verticalScrollBar().rangeChanged.connect(shell.on_scroll_range)
    scroll.verticalScrollBar().valueChanged.connect(shell.sync_fades)
    side_lay.addWidget(scroll, 1)

    side = SIDE_PAD - NAV_INSET
    lay = kit.vbox(margins=(side, 0, side, BODY_BOTTOM), spacing=0)
    body.setLayout(lay)
    _gap(shell, lay, GAP_TOP)

    # Identity
    w.set_logo_size(LOGO[ROOMY])
    identity_row = kit.hbox(w.logo_label, w.title_label, None, spacing=10)
    identity = _inset(identity_row, spacing=0)
    _gap(shell, identity, GAP_TAGLINE)
    identity.addWidget(w.tagline_label)
    lay.addLayout(identity)
    _gap(shell, lay, GAP_IDENTITY)

    # Preset. The stock minimum (28 x "X" plus the drop-down counted twice) is ~25px wider
    # than the names need; size the combo to the names instead so the sidebar can stay
    # slim. The names come from the preset store: the combo is still empty at startup.
    names = [p.name for p in w.presets.builtin] + [f"{p.name}  ★" for p in w.presets.user]
    names += [w.preset_combo.itemText(i) for i in range(w.preset_combo.count())]
    shell.preset_width = _preset_width(w.preset_combo, names)
    w.preset_combo.setMinimumContentsLength(16)
    w.preset_combo.setMinimumWidth(shell.preset_width)
    w.preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    preset_label = _side_label("Preset", body)
    lay.addLayout(_section(shell, preset_label, w.preset_combo, w.preset_hint))
    _gap(shell, lay, GAP_SECTION)

    # Save to
    w.browse_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    w.open_folder_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    folder_label = _side_label("Save to", body)
    lay.addLayout(_section(
        shell, folder_label, w.folder_edit, kit.hbox((w.browse_btn, 3), (w.open_folder_btn, 2), spacing=6),
    ))
    _gap(shell, lay, GAP_SECTION)

    # Options: clickable rows, their check boxes on the field edge
    options_label = _side_label("Options", body)
    lay.addLayout(_inset(options_label, spacing=0))
    _gap(shell, lay, GAP_OPTIONS)
    for check in (w.playlist_check, w.subs_check, w.meta_check):
        kit.set_role(check, "nav")
        lay.addWidget(check)
    _gap(shell, lay, GAP_BEFORE_ADVANCED)

    # Advanced
    advanced_rule = kit.divider(body)
    lay.addLayout(_inset(advanced_rule, spacing=0))
    _gap(shell, lay, GAP_DIVIDER)
    kit.set_role(w.advanced_toggle, "nav")
    w.advanced_toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    lay.addWidget(w.advanced_toggle)
    w.advanced_panel.set_stacked(True)
    lay.addLayout(_inset(w.advanced_panel, spacing=0))
    lay.addStretch(1)

    # Footer: Settings, pinned to the bottom of the sidebar (outside the scrolling part)
    spacing, bottom = FOOTER[ROOMY]
    footer = kit.vbox(spacing=spacing, margins=(side, 0, side, bottom))
    footer_rule = kit.divider(sidebar)
    footer.addLayout(_inset(footer_rule, spacing=0))
    kit.set_role(w.settings_button, "nav")
    w.settings_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    w.settings_button.setIconSize(QSize(SETTINGS_ICON_WIDTH, 16))
    footer.addWidget(w.settings_button)
    side_lay.addLayout(footer)

    root.addWidget(sidebar)

    # -------------------------------------------------------- main panel
    main = _Panel(shell, w.url_edit)
    mlay = kit.vbox(margins=(0, 0, 0, 0), spacing=0)
    main.setLayout(mlay)

    top = kit.vbox(margins=(PANEL_PAD, 16, PANEL_PAD, 18), spacing=10)
    top.addLayout(kit.hbox(
        kit.title("New download", main, "pageTitle"), None, w.clear_links_btn, w.paste_btn, spacing=6,
    ))
    w.url_edit.setMinimumHeight(LINKS_MIN)
    w.url_edit.setMaximumHeight(LINKS_MAX)
    top.addWidget(w.url_edit)
    # The action row says where the choices live: the sidebar on the left.
    hint = kit.muted("Uses the preset, folder and options on the left.", main, wrap=True)
    hint.setObjectName("actionHint")
    hint.setMinimumWidth(150)
    hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    w.download_btn.setMaximumWidth(280)
    w.download_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    top.addLayout(kit.hbox((hint, 3), None, w.cancel_btn, (w.download_btn, 2), spacing=8))
    mlay.addLayout(top)
    mlay.addWidget(kit.divider(main))

    # Downloads
    head = kit.hbox(
        kit.title("Downloads", main, "sectionTitle"), 4, w.summary_label, None, w.clear_finished_btn,
        spacing=8, margins=(PANEL_PAD, 12, PANEL_PAD - 6, 4),
    )
    mlay.addLayout(head)
    w.queue.setMinimumHeight(100)
    queue_box = kit.vbox((w.queue, 1), margins=(PANEL_PAD - 10, 0, PANEL_PAD - 10, 0))
    mlay.addLayout(queue_box, 3)
    log_head = kit.hbox(
        w.log_toggle, None, w.copy_log_btn, w.clear_log_btn,
        spacing=4, margins=(PANEL_PAD - 4, 6, PANEL_PAD - 6, 8),
    )
    mlay.addLayout(log_head)
    log_box = kit.vbox((w.log_view, 1), margins=(PANEL_PAD - 4, 0, PANEL_PAD - 4, 18))
    mlay.addLayout(log_box, 2)
    main.padded = [
        (top, 0, 16, 0, 18),
        (head, 0, 12, -6, 4),
        (log_head, -4, 6, -6, 8),
        (log_box, -4, 0, -4, 18),
    ]
    main.queue_box = queue_box

    root.addWidget(main, 1)

    # Error banner: full window width, so its text has room even in a small window
    banner_box = kit.vbox(w.banner, margins=(PANEL_GAP, 0, 0, 0))
    frame.addLayout(banner_box)

    shell.frame, shell.banner_box, shell.banner, shell.hint = frame, banner_box, w.banner, hint
    shell.sidebar, shell.scroll, shell.body, shell.body_lay = sidebar, scroll, body, lay
    shell.footer, shell.footer_rule, shell.settings = footer, footer_rule, w.settings_button
    shell.identity_row, shell.title, shell.set_logo = identity_row, w.title_label, w.set_logo_size
    shell.combo = w.preset_combo
    shell.advanced_toggle, shell.advanced_panel = w.advanced_toggle, w.advanced_panel
    shell.snaps = [preset_label, folder_label, options_label, advanced_rule]
    shell.fade_top, shell.fade_bottom = _Fade(sidebar, top=True), _Fade(sidebar, top=False)
    _ColumnFitter(shell, w.queue)
    fit_columns(w.queue, max(200, w.queue.viewport().width()))


# ------------------------------------------------------------------------ style

HUE_SHIFT = 20  # the Download gradient runs from indigo to this much further, violet


def _hue_shift(color: str, degrees: int) -> str:
    c = QColor(color)
    h, s, v, a = c.getHsv()
    c.setHsv((h + degrees) % 360, s, v, a)
    return c.name()


def _gradient_end(t: Theme, start: str) -> str:
    """The violet end of the Download gradient; darkened in dark mode so white text stays >= 4.5:1."""
    end = _hue_shift(start, HUE_SHIFT)
    return mix(end, "#000000", 0.1) if t.dark else end


def _button_base(t: Theme) -> str:
    """Download button colour: in dark mode the (lighter) accent is too pale under white text."""
    return t.accent_pressed if t.dark else t.accent


def _disabled_text(t: Theme) -> str:
    return mix(t.accent, t.card, 0.3 if t.dark else 0.38)


def _padded_icon(name: str, color: QColor) -> QIcon:
    """A glyph with transparent room on its right: Qt puts only 2px between icon and text."""
    icon = QIcon()
    for size in (16, 20, 24, 32):
        width = round(size * SETTINGS_ICON_WIDTH / 16)
        canvas = QPixmap(width, size)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap(QRectF(0, 0, size, size), glyph_pixmap(name, color, size, 1.0), QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(canvas, QIcon.Mode.Normal)
        icon.addPixmap(canvas, QIcon.Mode.Active)
    return icon


def after_refresh(w: MainWindow, t: Theme) -> None:
    """Settings gets a nav-row icon with a proper gap; the disabled Download keeps its icon legible."""
    w.settings_button.setIcon(_padded_icon("settings", t.color("muted")))
    w.download_btn.setIcon(glyph_icon("download", t.color("on_accent"), QColor(_disabled_text(t))))


def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:
    base = _button_base(t)
    # Dark mode: hover lifts the colour only slightly, so the white label keeps its contrast.
    base_hover = mix(base, t.accent, 0.2) if t.dark else t.accent_hover
    base_pressed = mix(t.accent_pressed, t.card, 0.15) if t.dark else t.accent_pressed
    violet = _gradient_end(t, base)
    violet_hover = _gradient_end(t, base_hover)
    violet_pressed = _gradient_end(t, base_pressed)
    nav_hover = mix(t.window, t.text, 0.06)
    nav_pressed = mix(t.window, t.text, 0.10)
    check_border = mix(t.window, t.text, 0.30 if t.dark else 0.24)
    chevron = glyph("chevron", t.muted)
    return f"""
QWidget#shell, QWidget#sideBody, QScrollArea#sideScroll {{ background: transparent; border: none; }}
QFrame#sidebar {{ background: transparent; border: none; }}
QFrame#sidebar QLabel {{ background: transparent; }}
QScrollArea#sideScroll QScrollBar:vertical {{
    background: transparent; width: {SIDE_SCROLLBAR}px; margin: 6px 5px 6px 0;
}}
QScrollArea#sideScroll QScrollBar::handle:vertical {{ margin: 0; border-radius: 2px; min-height: 40px; }}
QFrame#sidebar QComboBox {{ padding-right: {COMBO_PAD_RIGHT}px; }}
QFrame#sidebar QComboBox::drop-down {{ width: {COMBO_DROP}px; }}

QLabel#appTitle {{ font-size: {m.app_title_size}pt; font-weight: 700; color: {t.text}; }}
QLabel#appTagline {{ font-size: 9pt; color: {t.muted}; }}
QLabel#sideLabel {{ font-size: 8pt; font-weight: 700; color: {t.muted}; }}
QFrame#sidebar QLabel#muted {{ font-size: 9pt; color: {t.muted}; }}
QFrame#sidebar QLabel#fieldLabel {{
    font-size: 9pt; font-weight: 600; color: {t.muted}; padding-top: 6px; qproperty-indent: 0;
}}

QFrame#sidebar QCheckBox[role="nav"] {{
    padding: 4px {NAV_INSET}px; border-radius: 7px; spacing: 8px;
}}
QFrame#sidebar QCheckBox[role="nav"]:hover {{ background: {nav_hover}; }}
QFrame#sidebar QCheckBox::indicator:unchecked {{ border-color: {check_border}; }}
QFrame#sidebar QCheckBox::indicator:unchecked:hover {{ border-color: {t.accent}; }}
QFrame#sidebar QToolButton#sectionToggle[role="nav"] {{
    padding: 5px 8px 5px {NAV_INSET - 2}px; border-radius: 7px;
}}
QFrame#sidebar QToolButton#sectionToggle[role="nav"]:hover {{ background: {nav_hover}; color: {t.text}; }}
QFrame#sidebar QToolButton#sectionToggle[role="nav"]:checked {{ color: {t.text}; }}
QFrame#sidebar QToolButton#menuButton[role="nav"] {{
    background: transparent; border: none; border-radius: 7px; color: {t.text};
    padding: 6px 28px 6px {NAV_INSET - 2}px; font-weight: 600;
}}
QFrame#sidebar QToolButton#menuButton[role="nav"]:hover {{ background: {nav_hover}; }}
QFrame#sidebar QToolButton#menuButton[role="nav"]:pressed,
QFrame#sidebar QToolButton#menuButton[role="nav"]:open {{ background: {nav_pressed}; }}
QFrame#sidebar QToolButton#menuButton[role="nav"]::menu-indicator {{
    image: url("{chevron}"); subcontrol-origin: padding; subcontrol-position: center right;
    right: 10px; width: 10px; height: 10px;
}}
QFrame#sidebar QFrame#divider {{ background: {t.border}; }}

QFrame#mainPanel {{
    background: {t.card}; border: 1px solid {t.border}; border-radius: {m.card_radius + 2}px;
}}
QFrame#mainPanel QLabel {{ background: transparent; }}
QLabel#actionHint {{ font-size: 9pt; color: {t.muted}; }}
QLabel#pageTitle {{ font-size: 15pt; font-weight: 600; color: {t.text}; }}
QLabel#sectionTitle {{ font-size: 12pt; font-weight: 600; color: {t.text}; }}

QFrame#mainPanel QPlainTextEdit#links {{
    background: {t.alt}; border: 1px solid {t.border}; border-radius: 12px; padding: 7px 8px;
}}
QFrame#mainPanel QPlainTextEdit#links:hover {{ border-color: {t.border_strong}; }}
QFrame#mainPanel QPlainTextEdit#links:focus {{ background: {t.card}; border-color: {t.accent}; }}
QFrame#mainPanel QPlainTextEdit#links[dragActive="true"] {{
    border: 2px dashed {t.accent}; background: {t.accent_soft};
}}

QFrame#mainPanel QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {base}, stop:1 {violet});
    border-radius: {m.primary_radius}px;
}}
QFrame#mainPanel QPushButton#primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {base_hover}, stop:1 {violet_hover});
}}
QFrame#mainPanel QPushButton#primary:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {base_pressed}, stop:1 {violet_pressed});
}}
QFrame#mainPanel QPushButton#primary:disabled {{ background: {t.accent_soft}; color: {_disabled_text(t)}; }}
QFrame#mainPanel QPushButton#secondary {{ background: {t.card}; padding: {m.primary_padding[0]}px 16px; }}
QFrame#mainPanel QPushButton#secondary:hover {{ background: {t.hover}; }}
QPushButton#accentSmall {{ background: {base}; }}
QPushButton#accentSmall:hover {{ background: {base_hover}; }}

QFrame#mainPanel QTreeWidget#queue {{ background: {t.card}; }}
QFrame#mainPanel QTreeWidget#queue QScrollBar:vertical {{ width: 9px; }}
QFrame#mainPanel QHeaderView::section {{
    background: {t.card}; color: {t.muted}; font-size: 9pt; font-weight: 600;
    border: none; border-bottom: 1px solid {t.border}; padding: 7px 6px;
}}
QFrame#mainPanel QHeaderView::section:first {{ padding-left: 10px; }}
QFrame#mainPanel QPlainTextEdit#log {{ border-radius: 10px; }}
"""


VARIANT = Variant(
    key="sidebar",
    name="Sidebar",
    description="Two-pane app layout: choices in a tinted sidebar, links and downloads in a large panel, indigo accent.",
    light=LIGHT,
    dark=DARK,
    metrics=METRICS,
    build_layout=build,
    extra_qss=extra_qss,
    after_refresh=after_refresh,
)
