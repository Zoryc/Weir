"""Steps: a guided 1-2-3 flow with big numbered badges and a friendly teal accent.

Step 1 (paste) spans the width; Step 2 (choose) and Step 3 (download) sit side by side
so the eye moves paste -> choose -> download in a Z. When the page is too narrow for
both without squeezing the folder field, Step 3 turns into a full-width bar under
Step 2. "Your downloads" fills the rest of the window.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QBoxLayout,
    QGridLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QTreeWidget,
    QWidget,
)

from .. import kit
from ..theme import GlyphFile, Metrics, Theme, Variant, css_families, mix

if TYPE_CHECKING:
    from ..main_window import MainWindow

# Accent is a true teal (hue ~181), success a leafy yellow-green (hue ~110): in-progress
# and finished downloads stay clearly different colours. Neutrals carry a hint of teal.
LIGHT = Theme(
    dark=False,
    window="#edf3f2",
    card="#ffffff",
    alt="#f3f8f7",
    hover="#e6f0ef",
    border="#d6e3e1",
    border_strong="#bed0cd",
    text="#142d2b",
    muted="#566d6a",
    accent="#0b7d80",
    accent_hover="#096f72",
    accent_pressed="#075f62",
    accent_disabled="#a5d3d4",
    accent_soft="#d8eeee",
    selection="#cfe9e9",
    track="#dce9e8",
    success="#368d26",
    warning="#b46e07",
    danger="#d23a2e",
    scroll="#bacbc9",
)

DARK = Theme(
    dark=True,
    window="#0e1716",
    card="#16211f",
    alt="#1b2725",
    hover="#21302e",
    border="#263634",
    border_strong="#374a47",
    text="#e2eeec",
    muted="#8fa7a3",
    accent="#3cc3c5",
    accent_hover="#56d0d2",
    accent_pressed="#2eaaac",
    accent_disabled="#21423f",
    accent_soft="#153536",
    selection="#1b3e3e",
    track="#293a38",
    success="#87d46c",
    warning="#f2b54f",
    danger="#ff6d61",
    scroll="#3a4d4a",
    on_accent="#032224",
)

_UI_FONTS = ("Segoe UI Variable Text", "Segoe UI", "Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans")
_HEADING_FONTS = ("Segoe UI Variable Display", "Segoe UI", "Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans")
_MONO_FONTS = ("Cascadia Mono", "Consolas", "Noto Sans Mono", "Ubuntu Mono", "DejaVu Sans Mono")

METRICS = Metrics(
    font_families=_UI_FONTS,
    font_size=10.0,
    heading_families=_HEADING_FONTS,
    mono_families=_MONO_FONTS,
    app_title_size=15.0,
    card_title_size=13.0,
    heading_weight=600,
    card_radius=18,
    control_radius=10,
    primary_radius=14,
    control_padding=(7, 10),
    button_padding=(6, 15),
    primary_padding=(12, 22),
    primary_font_size=12.0,
    indicator_size=18,
    row_extra=22,
)

BADGE = 42  # step number circle, px
GUTTER = 14  # gap between the badge column and the step's content
CARD_MARGINS = (20, 12, 20, 14)
FORM_SPACING = 16  # label -> field; same as the advanced panel so both columns line up
FOLDER_MIN = 300  # the save-to path must stay readable (Step 3 moves underneath otherwise)
QUEUE_ROWS = 3  # the list always shows at least this many downloads (the page scrolls instead)
QWIDGETSIZE_MAX = 16777215


# ------------------------------------------------------------------ helpers


class _FlowLayout(QLayout):
    """Left-to-right layout that wraps onto a new line when it runs out of width."""

    def __init__(self, parent: QWidget | None = None, h_spacing: int = 20, v_spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h = h_spacing
        self._v = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # type: ignore[override]
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # type: ignore[override]
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        visible = [item for item in self._items if not item.isEmpty()]
        width = sum(item.sizeHint().width() for item in visible) + self._h * max(0, len(visible) - 1)
        height = max((item.sizeHint().height() for item in visible), default=0)
        return QSize(width, height)

    def minimumSize(self) -> QSize:
        size = QSize(0, 0)
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect: QRect, apply: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x, y, line = rect.x(), y + line + self._v, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line = max(line, hint.height())
        return y + line - rect.y()


class _ChooseAndGo(QWidget):
    """Step 2 and Step 3 side by side. If the page is too narrow for both (the folder
    field would get cramped) Step 3 becomes a full-width bar below Step 2."""

    GO_MIN = 250
    GO_MAX = 290
    GO_SHARE = 0.25

    def __init__(self, parent: QWidget, choose: QWidget, go: QWidget, go_box: QBoxLayout,
                 actions: QBoxLayout, download: QWidget, advanced: QWidget,
                 choose_min: Callable[[], int], spacing: int) -> None:
        super().__init__(parent)
        self._choose = choose
        self._go = go
        self._go_box = go_box
        self._actions = actions
        self._download = download
        self._advanced = advanced
        self._choose_min = choose_min
        self._viewport: QWidget | None = None
        self._wide: bool | None = None
        self._top: bool | None = None
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(spacing)
        self._box.addWidget(choose, 1)
        self._box.addWidget(go, 0)
        self._apply(True, self.GO_MIN)

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Resize, QEvent.Type.Show):
            self._update_mode()
        return handled

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._viewport and event.type() == QEvent.Type.Resize:
            self._update_mode()
        return False

    def _available_width(self) -> int:
        """Width the row really gets on screen: the page can be wider than the viewport
        while both cards are side by side and need more room than there is."""
        page = self.parentWidget()
        viewport = page.parentWidget() if page is not None else None
        if page is None or viewport is None:
            return self.width()
        if viewport is not self._viewport:
            self._viewport = viewport
            viewport.installEventFilter(self)
        margin = self.mapTo(page, QPoint(0, 0)).x()
        return viewport.width() - 2 * margin

    def _update_mode(self) -> None:
        if not self.isAncestorOf(self._download):  # parts already moved to a newer layout
            return
        available = self._available_width()
        go_need = self._download.sizeHint().width() + CARD_MARGINS[0] + CARD_MARGINS[2]
        go_width = max(self.GO_MIN, min(self.GO_MAX, round(available * self.GO_SHARE)), go_need)
        choose_width = max(self._choose_min(), self._choose.minimumSizeHint().width())
        need = choose_width + self._box.spacing() + go_width
        self._apply(need <= available, go_width)

    def _apply(self, wide: bool, go_width: int) -> None:
        if wide:
            self._go.setFixedWidth(go_width)
        else:
            self._go.setMinimumWidth(0)
            self._go.setMaximumWidth(QWIDGETSIZE_MAX)
        # Beside a much taller Step 2 (Advanced options open) Step 3 keeps its own height
        # instead of becoming a tall empty panel; otherwise both cards end on one line.
        top = wide and self._advanced.isVisibleTo(self._choose)
        if top != self._top:
            self._top = top
            self._box.setAlignment(self._go, Qt.AlignmentFlag.AlignTop if top else Qt.AlignmentFlag(0))
        if wide == self._wide:
            return
        self._wide = wide
        d = QBoxLayout.Direction
        self._box.setDirection(d.LeftToRight if wide else d.TopToBottom)
        self._go_box.setDirection(d.TopToBottom if wide else d.LeftToRight)
        self._go_box.setSpacing(16 if wide else 24)
        # go_box holds [heading, buttons, spacer]. As a card the spacer keeps the buttons
        # right under the heading; as a bar the heading takes the free width and the
        # buttons sit on the right.
        self._go_box.setStretch(0, 0 if wide else 1)
        self._go_box.setStretch(2, 1 if wide else 0)
        self._actions.setDirection(d.TopToBottom if wide else d.RightToLeft)
        self._actions.setSpacing(8 if wide else 10)
        self._download.setMinimumWidth(0 if wide else 220)


class _FollowShown(QObject):
    """Shows ``follower`` (a decorative label) only while ``watched`` is shown."""

    def __init__(self, watched: QWidget, follower: QWidget) -> None:
        super().__init__(follower)
        self._watched = watched
        self._follower = follower
        watched.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._watched and event.type() in (
            QEvent.Type.ShowToParent, QEvent.Type.HideToParent, QEvent.Type.Show, QEvent.Type.Hide,
        ):
            self._follower.setVisible(not self._watched.isHidden())
        return False


class _FitColumns(QObject):
    """Sizes the queue's Preset and Progress columns from the list width, so Title keeps
    the most room on small windows and Preset is still readable on large ones."""

    FIXED = ((3, 80), (4, 56), (5, 118))  # Speed, ETA, Status (fits "Downloading")

    def __init__(self, queue: QTreeWidget, owner: QWidget) -> None:
        super().__init__(owner)
        self._queue = queue
        queue.installEventFilter(self)
        for column, width in self.FIXED:
            queue.setColumnWidth(column, width)
        self._fit(queue.width())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._queue and event.type() == QEvent.Type.Resize:
            self._fit(self._queue.width())
        return False

    def _fit(self, width: int) -> None:
        self._queue.setColumnWidth(1, max(112, min(170, round(width * 0.17))))  # Preset
        self._queue.setColumnWidth(2, max(112, min(150, round(width * 0.15))))  # Progress


def _badge(number: int, parent: QWidget) -> QLabel:
    badge = QLabel(str(number), parent)
    badge.setObjectName("stepBadge")
    badge.setFixedSize(BADGE, BADGE)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return badge


def _heading(title: str, note: str, parent: QWidget) -> QBoxLayout:
    note_label = kit.muted(note, parent, wrap=True)
    return kit.vbox(kit.title(title, parent, "stepTitle"), note_label, spacing=1)


def _step_card(content: QWidget, number: int, title: str, note: str,
               *tail: kit.Item) -> tuple[QWidget, QGridLayout]:
    """Card with a big number badge in a left gutter, the heading (plus any tail widgets
    on the right) beside it, and the step's own controls underneath the heading."""
    card = kit.card(content)
    grid = QGridLayout(card)
    grid.setContentsMargins(*CARD_MARGINS)
    grid.setHorizontalSpacing(GUTTER)
    grid.setVerticalSpacing(10)
    grid.setColumnStretch(1, 1)
    grid.addWidget(_badge(number, card), 0, 0, Qt.AlignmentFlag.AlignTop)
    header = kit.hbox((_heading(title, note, card), 1), *tail, spacing=8)
    header.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    grid.addLayout(header, 0, 1)
    return card, grid


# ------------------------------------------------------------------- layout


def build(w: MainWindow, content: QWidget) -> None:
    root = kit.vbox(spacing=10, margins=(20, 10, 20, 8))
    content.setLayout(root)

    # Header: teal app tile, name ... Settings
    logo = QLabel(content)
    logo.setObjectName("stepsLogo")
    logo.setFixedSize(34, 34)
    root.addLayout(kit.hbox(logo, 4, w.title_label, None, w.settings_button, spacing=8))

    # Step 1 - paste
    kit.set_role(w.paste_btn, "soft")
    paste, paste_grid = _step_card(
        content, 1, "Paste your links", "Videos, playlists or songs, one link per line.",
        w.clear_links_btn, w.paste_btn,
    )
    w.url_edit.setMinimumHeight(58)
    w.url_edit.setMaximumHeight(62)
    paste_grid.addWidget(w.url_edit, 1, 1)
    root.addWidget(paste)

    # Step 2 - choose
    kit.set_role(w.advanced_toggle, "head")
    choose, choose_grid = _step_card(
        content, 2, "Choose what you want", "Pick a format and where to save it.",
        w.advanced_toggle,
    )
    form = QGridLayout()
    form.setHorizontalSpacing(FORM_SPACING)
    form.setVerticalSpacing(10)
    form.setColumnStretch(1, 1)
    format_label = kit.field_label("Format", choose)
    format_label.setContentsMargins(0, 9, 0, 0)  # centre on the combo, not on combo + hint
    folder_label = kit.field_label("Save to", choose)
    options_label = kit.field_label("Options", choose)
    options_label.setContentsMargins(0, 1, 0, 0)
    w.preset_combo.setMinimumContentsLength(20)
    form.addWidget(format_label, 0, 0, Qt.AlignmentFlag.AlignTop)
    form.addLayout(kit.vbox(w.preset_combo, w.preset_hint, spacing=4), 0, 1)
    form.addWidget(folder_label, 1, 0)
    folder_row = kit.hbox((w.folder_edit, 1), w.browse_btn, w.open_folder_btn)
    form.addLayout(folder_row, 1, 1)
    form.addWidget(options_label, 2, 0, Qt.AlignmentFlag.AlignTop)
    options = _FlowLayout(h_spacing=18, v_spacing=10)
    for check in (w.playlist_check, w.subs_check, w.meta_check):
        options.addWidget(check)
    form.addLayout(options, 2, 1)
    choose_grid.addLayout(kit.vbox(form, w.advanced_panel, spacing=6), 1, 1)

    def choose_min() -> int:
        """Width Step 2 needs so the save-to path keeps FOLDER_MIN px."""
        buttons = w.browse_btn.sizeHint().width() + w.open_folder_btn.sizeHint().width()
        return (CARD_MARGINS[0] + CARD_MARGINS[2] + BADGE + GUTTER + folder_label.minimumWidth()
                + FORM_SPACING + FOLDER_MIN + 2 * folder_row.spacing() + buttons)

    # Step 3 - download (a card beside Step 2, or a bar below it when narrow)
    go = kit.card(content, "stepGo")
    go_box = QBoxLayout(QBoxLayout.Direction.TopToBottom, go)
    go_box.setContentsMargins(*CARD_MARGINS)
    head = kit.hbox(
        _badge(3, go),
        (_heading("Download", "Files are saved to the folder from step 2.", go), 1),
        spacing=GUTTER,
    )
    head.setAlignment(Qt.AlignmentFlag.AlignTop)
    go_box.addLayout(head)
    kit.set_role(w.download_btn, "go")
    kit.set_role(w.cancel_btn, "go")
    w.download_btn.setIconSize(QSize(20, 20))
    actions = QBoxLayout(QBoxLayout.Direction.TopToBottom)
    actions.addWidget(w.download_btn)
    actions.addWidget(w.cancel_btn)
    go_box.addLayout(actions)
    go_box.addStretch(1)
    root.addWidget(_ChooseAndGo(content, choose, go, go_box, actions, w.download_btn,
                                w.advanced_panel, choose_min, spacing=10))

    # Your downloads
    downloads = kit.card(content)
    dlay = kit.vbox(spacing=10, margins=(20, 10, 20, 13))
    downloads.setLayout(dlay)
    icon = QLabel(downloads)
    icon.setObjectName("stepsListIcon")
    icon.setFixedSize(34, 34)
    icon_column = kit.hbox(icon)
    icon_column.setAlignment(Qt.AlignmentFlag.AlignCenter)
    holder = QWidget(downloads)  # badge-wide column, so the title lines up with the step titles
    holder.setFixedWidth(BADGE)
    holder.setLayout(icon_column)
    dlay.addLayout(kit.hbox(
        holder, GUTTER - 8, kit.title("Your downloads", downloads, "stepTitle"), 10, w.summary_label,
        None, w.clear_finished_btn, w.log_toggle, spacing=8,
    ))
    # The list keeps room for a few downloads; on small windows the page scrolls instead.
    fm = QFontMetrics(w.queue.font())
    row = fm.height() * 2 + METRICS.row_extra
    w.queue.setMinimumHeight(QUEUE_ROWS * row + fm.height() + 16)
    w.log_view.setMinimumHeight(140)
    dlay.addWidget(w.queue, 3)
    dlay.addWidget(w.banner)
    # Caption for the log box, shown only while the log is open.
    caption = QWidget(downloads)
    caption.setLayout(kit.hbox(
        kit.field_label("Log", caption),
        kit.muted("yt-dlp's own messages, useful when asking for help", caption),
        spacing=8,
    ))
    caption.setVisible(w.log_toggle.isChecked())
    _FollowShown(w.log_view, caption)
    dlay.addLayout(kit.hbox(caption, None, w.copy_log_btn, w.clear_log_btn, spacing=4))
    dlay.addWidget(w.log_view, 2)
    root.addWidget(downloads, 1)

    _FitColumns(w.queue, downloads)
    kit.align_labels([format_label, folder_label, options_label, *w.advanced_panel.labels])


def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:
    heading = f"font-family: {css_families(m.heading_families)};"
    go_bg = mix(t.card, t.accent, 0.13 if t.dark else 0.09)
    go_border = mix(t.card, t.accent, 0.36 if t.dark else 0.40)
    soft_text = t.accent_hover if t.dark else t.accent_pressed
    disabled_bg = mix(t.card, t.accent, 0.16 if t.dark else 0.18)
    disabled_text = mix(disabled_bg, t.text, 0.50 if t.dark else 0.56)  # ~3.3:1, still reads as off
    return f"""
QLabel#appTitle {{ font-weight: 700; }}
QLabel#stepsLogo {{
    background-color: {t.accent}; border-radius: 11px;
    background-image: url("{glyph('download', t.on_accent)}");
    background-position: center; background-repeat: no-repeat;
}}
QFrame#card QLabel#stepsListIcon {{
    background: {t.accent_soft}; border-radius: 17px; padding: 8px;
    image: url("{glyph('inbox', t.accent)}");
}}
QLabel#stepBadge, QFrame#card QLabel#stepBadge, QFrame#stepGo QLabel#stepBadge {{
    background: {t.accent}; color: {t.on_accent};
    border: 4px solid {t.accent_soft}; border-radius: {BADGE // 2}px; padding: 0;
    font-size: 14pt; font-weight: 700; {heading}
}}
QLabel#stepTitle {{ font-size: {m.card_title_size}pt; font-weight: 600; color: {t.text}; {heading} }}

QFrame#stepGo {{ background: {go_bg}; border: 1px solid {go_border}; border-radius: {m.card_radius}px; }}
QFrame#stepGo QLabel {{ background: transparent; }}
QFrame#stepGo QLabel#stepBadge {{ background: {t.accent}; border-color: {t.card}; }}

QPlainTextEdit#links {{ background: {t.alt}; border-radius: 12px; padding: 6px 6px; }}
QPlainTextEdit#links:focus {{ background: {t.card}; }}
QCheckBox::indicator {{ border-radius: 6px; }}
QToolButton#sectionToggle[role="head"] {{
    border: 1px solid {t.border_strong}; border-radius: 15px; padding: 5px 4px 5px 10px;
}}
QToolButton#sectionToggle[role="head"]:hover {{ background: {t.accent_soft}; border-color: {go_border}; }}
QToolButton#sectionToggle[role="head"]:checked {{
    background: {t.accent_soft}; border-color: {t.accent_soft}; color: {soft_text};
}}

QPushButton[role="soft"] {{
    background: {t.accent_soft}; color: {soft_text}; border: 1px solid {t.accent_soft};
    font-weight: 600; padding: 6px 16px;
}}
QPushButton[role="soft"]:hover {{ border-color: {go_border}; }}
QPushButton[role="soft"]:pressed {{ background: {go_border}; }}
QPushButton[role="soft"]:focus {{ border-color: {t.accent}; }}
QPushButton#primary[role="go"] {{
    min-height: 26px; padding: 12px 18px; border-radius: 24px; font-size: 12.5pt;
}}
QPushButton#primary:disabled {{ background: {disabled_bg}; color: {disabled_text}; }}
QPushButton#secondary[role="go"] {{ min-height: 22px; padding: 8px 18px; border-radius: 19px; font-size: 10.5pt; }}
"""


VARIANT = Variant(
    key="steps",
    name="Steps",
    description="A guided 1-2-3 flow with big numbered steps and a friendly teal accent.",
    light=LIGHT,
    dark=DARK,
    metrics=METRICS,
    build_layout=build,
    extra_qss=extra_qss,
)
