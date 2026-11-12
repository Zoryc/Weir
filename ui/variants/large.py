"""Large & Clear: accessibility-first layout for low vision and first-time computer users.

Big 13 pt text, near-black on white (true white on black in dark mode), 2 px borders,
thick focus rings and tall controls. One simple column of numbered steps —
"1 Paste the link", "2 Choose the format", "3 Start" — ends in a full-width Download
bar; the downloads list follows under a clear rule. A black title band with a blue
stripe keeps the look recognisable in both schemes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QResizeEvent
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QLayout, QLayoutItem, QTreeWidget, QWidget

from .. import kit
from ..icon import glyph_icon
from ..theme import GlyphFile, Metrics, Theme, Variant, mix

if TYPE_CHECKING:
    from ..main_window import MainWindow

LIGHT = Theme(
    dark=False,
    window="#ffffff",
    card="#ffffff",
    alt="#f3f2f1",
    hover="#e8eaec",
    border="#b1b4b6",
    border_strong="#0b0c0c",
    text="#0b0c0c",
    muted="#474e53",  # 8.5:1 on white
    accent="#1452c4",  # 6.9:1 with white text
    accent_hover="#0f44a6",
    accent_pressed="#0b3585",
    accent_disabled="#c9d6ee",
    accent_soft="#e4ecfa",
    selection="#d3e1fa",
    track="#d4d6d8",
    success="#00703c",
    warning="#a65200",
    danger="#c0221b",
    scroll="#6f777b",
)

DARK = Theme(
    dark=True,
    window="#000000",
    card="#000000",
    alt="#161616",
    hover="#262626",
    border="#6e6e6e",
    border_strong="#e6e6e6",
    text="#ffffff",
    muted="#c6c6c6",  # 12:1 on black
    accent="#5cacff",  # 8.8:1 against black text
    accent_hover="#86c2ff",
    accent_pressed="#3a92f0",
    accent_disabled="#1d3552",
    accent_soft="#0e2238",
    selection="#123d6b",
    track="#3d3d3d",
    success="#4ade80",
    warning="#fbbf24",
    danger="#ff6b63",
    scroll="#8f8f8f",
    on_accent="#000000",
)

METRICS = Metrics(
    font_families=("Segoe UI", "Noto Sans", "DejaVu Sans", "Liberation Sans", "Arial"),
    font_size=13.0,
    mono_families=("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono"),
    app_title_size=18.0,
    card_title_size=17.0,
    heading_weight=700,
    card_radius=10,
    control_radius=6,
    primary_radius=10,
    control_padding=(9, 12),
    button_padding=(9, 18),
    primary_padding=(13, 28),
    primary_font_size=16.0,
    border_width=2,
    indicator_size=24,
    scrollbar_width=16,
    row_extra=20,
)

HEADER_LIGHT = "#0b0c0c"  # the title band is dark in both schemes
HEADER_DARK = "#1a1a1a"
ON_HEADER = "#ffffff"
FOCUS_YELLOW = "#ffdd00"  # high-visibility focus colour on dark backgrounds

STEP_GAP = 16  # space between the numbered steps
ROW_GAP = 10  # space between form rows
MARGIN = 30  # page margin left/right
BADGE = 36  # step number circle


class _Flow(QLayout):
    """Left-to-right row that wraps onto the next line when the window is narrow.

    It deliberately does not use height-for-width: the scrolling page would then lay
    everything out at its preferred height. Instead it remembers how tall it had to be
    at the last width and asks the parents for a relayout when that changes.
    """

    def __init__(self, h_spacing: int, v_spacing: int) -> None:
        super().__init__()
        self._items: list[QLayoutItem] = []
        self._h = h_spacing
        self._v = v_spacing
        self._height = 0  # wrapped height at the width we were last given
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt override
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def _visible(self) -> list[QLayoutItem]:
        return [item for item in self._items if not item.isEmpty()]

    def sizeHint(self) -> QSize:  # noqa: N802 - preferred: everything on one line
        visible = self._visible()
        if not visible:
            return QSize(0, 0)
        width = sum(item.sizeHint().width() for item in visible) + self._h * (len(visible) - 1)
        height = max(item.sizeHint().height() for item in visible)
        return QSize(width, max(height, self._height))

    def minimumSize(self) -> QSize:  # noqa: N802 - the widest single item, wrapped height
        size = QSize(0, 0)
        for item in self._visible():
            size = size.expandedTo(item.minimumSize())
        return QSize(size.width(), max(size.height(), self._height))

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        height = self._arrange(rect)
        if height != self._height:
            self._height = height
            self.update()  # posts a relayout so the rows below make room

    def _arrange(self, rect: QRect) -> int:
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._visible():
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x = rect.x()
                y += line_height + self._v
                line_height = 0
            item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


class _QueueHolder(QFrame):
    """Invisible frame around the downloads list.

    * Its *preferred* height is capped: when the window is too short the page lays
      everything out at its preferred height and scrolls, so capping the list's wish
      keeps the steps and the list on one screen at the default window size. The list
      still grows to fill any extra room.
    * It shares the width between the columns as the window is resized, so the title
      keeps most of the room on a small window and the preset name is not cut short
      on a big one.
    """

    def __init__(self, parent: QWidget, queue: QTreeWidget, preferred: int) -> None:
        super().__init__(parent)
        self.setObjectName("lcQueueHolder")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._queue = queue
        self._preferred = preferred
        self.setLayout(kit.vbox(queue, spacing=0))

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        return QSize(hint.width(), max(self.minimumSizeHint().height(), min(hint.height(), self._preferred)))

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fit_columns()

    def fit_columns(self) -> None:
        from ..main_window import COL_ETA, COL_PRESET, COL_PROGRESS, COL_SPEED, COL_STATUS

        queue = self._queue
        if not self.isAncestorOf(queue):  # the list already moved on to another layout
            return
        fixed = {COL_SPEED: 100, COL_ETA: 66, COL_STATUS: 150}
        room = self.width() - 4 - 16 - sum(fixed.values())  # frame and scroll bar
        widths = {
            COL_PRESET: max(100, min(220, round(room * 0.22))),
            COL_PROGRESS: max(116, min(180, round(room * 0.2))),
            **fixed,
        }
        for column, width in widths.items():
            queue.setColumnWidth(column, width)


def _badge(parent: QWidget, number: int) -> QLabel:
    badge = QLabel(str(number), parent)
    badge.setObjectName("lcBadge")
    badge.setFixedSize(BADGE, BADGE)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return badge


def _step_header(parent: QWidget, number: int, text: str, *trailing: QWidget) -> QLayout:
    """Round number badge + plain-language heading, optional controls on the right."""
    title = kit.title(text, parent, "lcStepTitle")
    return kit.hbox(_badge(parent, number), 14, title, None, *trailing, spacing=10)


def _top_offset(label: QLabel, beside: QWidget) -> None:
    """Shift a top-aligned label down so its text centres on the first line of ``beside``."""
    label.ensurePolished()
    beside.ensurePolished()
    offset = (beside.sizeHint().height() - label.sizeHint().height()) // 2
    label.setContentsMargins(0, max(0, offset), 0, 0)


def build(w: MainWindow, content: QWidget) -> None:
    root = kit.vbox(spacing=0, margins=(0, 0, 0, 0))
    content.setLayout(root)

    # Title band: logo, app name … Settings
    header = kit.panel(content, "lcHeader")
    w.set_logo_size(34)
    header.setLayout(kit.hbox(
        w.logo_label, 14, w.title_label, None, w.settings_button,
        spacing=0, margins=(MARGIN, 6, MARGIN, 6),
    ))
    root.addWidget(header)

    body = kit.vbox(spacing=0, margins=(MARGIN, 10, MARGIN, 6))
    root.addLayout(body, 1)

    # 1 — Paste the link
    kit.set_role(w.paste_btn, "lcPaste")
    body.addLayout(_step_header(content, 1, "Paste the link", w.clear_links_btn, w.paste_btn))
    body.addSpacing(8)
    w.url_edit.setMinimumHeight(72)
    w.url_edit.setMaximumHeight(80)
    body.addWidget(w.url_edit)
    body.addSpacing(STEP_GAP)

    # 2 — Choose the format, and 3 — Start, share one grid so the big Download
    # button lines up exactly with the fields above it.
    w.advanced_toggle.setIconSize(QSize(18, 18))
    format_label = kit.field_label("Format", content)
    folder_label = kit.field_label("Save to", content)
    options_label = kit.field_label("Options", content)
    _top_offset(format_label, w.preset_combo)
    _top_offset(options_label, w.playlist_check)

    options = _Flow(h_spacing=30, v_spacing=8)
    for check in (w.playlist_check, w.subs_check, w.meta_check):
        options.addWidget(check)

    w.download_btn.setIconSize(QSize(24, 24))
    start_gap = STEP_GAP - ROW_GAP + 4  # extra room above step 3 (row spacing comes on top)

    grid = QGridLayout()
    grid.setHorizontalSpacing(16)  # same as the Advanced panel, so its fields line up too
    grid.setVerticalSpacing(ROW_GAP)
    grid.setColumnStretch(1, 1)
    grid.addLayout(_step_header(content, 2, "Choose the format", w.advanced_toggle), 0, 0, 1, 2)
    grid.addWidget(format_label, 1, 0, Qt.AlignmentFlag.AlignTop)
    grid.addLayout(kit.vbox(w.preset_combo, w.preset_hint, spacing=4), 1, 1)
    grid.addWidget(folder_label, 2, 0)
    grid.addLayout(kit.hbox((w.folder_edit, 1), w.browse_btn, w.open_folder_btn, spacing=10), 2, 1)
    grid.addWidget(options_label, 3, 0, Qt.AlignmentFlag.AlignTop)
    grid.addLayout(options, 3, 1)
    grid.addWidget(w.advanced_panel, 4, 0, 1, 2)
    grid.addLayout(kit.hbox(_badge(content, 3), 14, kit.title("Start", content, "lcStepTitle"), None,
                            spacing=0, margins=(0, start_gap, 0, 0)), 5, 0)
    grid.addLayout(kit.vbox(w.download_btn, margins=(0, start_gap, 0, 0)), 5, 1)
    body.addLayout(grid)
    body.addSpacing(STEP_GAP - 3)

    # Downloads, under a rule
    body.addWidget(kit.panel(content, "lcRule"))
    body.addSpacing(8)
    kit.set_role(w.cancel_btn, "lcCancel")
    w.log_toggle.setIconSize(QSize(18, 18))
    downloads = kit.vbox(spacing=7)
    downloads.addLayout(kit.hbox(
        kit.title("Downloads", content, "lcSectionTitle"), 16, w.summary_label, None,
        w.clear_finished_btn, w.cancel_btn, spacing=10,
    ))
    w.queue.setMinimumHeight(156)  # room for the "No downloads yet" message
    holder = _QueueHolder(content, w.queue, preferred=162)
    holder.fit_columns()
    downloads.addWidget(w.banner)  # errors first, where they cannot be missed
    downloads.addWidget(holder, 3)
    downloads.addLayout(kit.hbox(w.log_toggle, None, w.copy_log_btn, w.clear_log_btn, spacing=6))
    downloads.addWidget(w.log_view, 2)
    body.addLayout(downloads, 1)

    kit.align_labels([format_label, folder_label, options_label, *w.advanced_panel.labels])


def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:
    cv, ch = m.control_padding
    bv, bh = m.button_padding
    pv, ph = m.primary_padding
    ring = FOCUS_YELLOW if t.dark else t.accent  # 3 px focus ring for fields and buttons
    primary_ring = FOCUS_YELLOW if t.dark else t.text
    header_bg = HEADER_DARK if t.dark else HEADER_LIGHT
    header_hover = mix(header_bg, ON_HEADER, 0.16)
    soft_danger = mix(t.card, t.danger, 0.10 if t.dark else 0.07)
    return f"""
QFrame#lcHeader {{ background: {header_bg}; border: none; border-bottom: 5px solid {t.accent}; }}
QFrame#lcHeader QLabel {{ background: transparent; color: {ON_HEADER}; }}
QFrame#lcHeader QToolButton#menuButton {{
    background: transparent; color: {ON_HEADER}; border: 2px solid {ON_HEADER};
    padding: 3px 36px 3px 14px; font-weight: 600;
}}
QFrame#lcHeader QToolButton#menuButton:hover {{ background: {header_hover}; }}
QFrame#lcHeader QToolButton#menuButton:focus {{
    border: 3px solid {FOCUS_YELLOW}; padding: 2px 35px 2px 13px;
}}
QFrame#lcHeader QToolButton#menuButton::menu-indicator {{
    image: url("{glyph('chevron', ON_HEADER)}"); right: 12px; width: 13px; height: 13px;
}}

QLabel#lcBadge {{
    background: {t.text}; color: {t.window}; border-radius: {BADGE // 2}px;
    font-size: 15pt; font-weight: 700;
}}
QLabel#lcStepTitle, QLabel#lcSectionTitle {{ font-size: {m.card_title_size}pt; font-weight: 700; color: {t.text}; }}
QFrame#lcRule {{ background: {t.border}; border: none; min-height: 2px; max-height: 2px; }}
QLabel#fieldLabel {{ color: {t.text}; font-weight: 700; }}
QLabel#muted {{ color: {t.muted}; }}

QPushButton {{ font-weight: 600; }}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:focus {{ border: 3px solid {ring}; padding: {bv - 1}px {bh - 1}px; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.alt}; border-color: {t.border}; }}
QPushButton[role="lcPaste"] {{ color: {t.accent}; border-color: {t.accent}; }}
QPushButton[role="lcPaste"]:hover {{ background: {t.accent_soft}; }}
QPushButton[role="lcPaste"]:focus {{ border-color: {primary_ring}; background: {t.accent_soft}; }}

QPushButton#primary {{
    background: {t.accent}; color: {t.on_accent}; border: 3px solid {t.accent};
    border-radius: {m.primary_radius}px; padding: {pv - 1}px {ph}px; min-height: 26px;
    font-size: {m.primary_font_size}pt; font-weight: 700;
}}
QPushButton#primary:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton#primary:pressed {{ background: {t.accent_pressed}; border-color: {t.accent_pressed}; }}
QPushButton#primary:focus {{ border-color: {primary_ring}; }}
QPushButton#primary:disabled {{ background: {t.alt}; color: {t.muted}; border: 3px solid {t.border}; }}

QPushButton#secondary {{
    padding: {bv}px {bh}px; border-radius: {m.control_radius}px; font-size: {m.font_size}pt;
}}
QPushButton#secondary[role="lcCancel"]:enabled {{ color: {t.danger}; border-color: {t.danger}; }}
QPushButton#secondary[role="lcCancel"]:enabled:hover {{ background: {soft_danger}; }}
QPushButton#secondary[role="lcCancel"]:enabled:focus {{ border-color: {ring}; }}
QPushButton#secondary:focus {{ border: 3px solid {ring}; padding: {bv - 1}px {bh - 1}px; }}

QPushButton#flat {{
    background: transparent; border: 2px solid transparent; color: {t.accent};
    padding: {bv - 2}px 10px; font-weight: 600; text-decoration: underline;
}}
QPushButton#flat:hover {{ background: {t.accent_soft}; color: {t.accent}; }}
QPushButton#flat:focus {{ border: 2px solid {ring}; }}
QPushButton#flat:disabled {{ color: {t.muted}; background: transparent; text-decoration: none; }}
QPushButton#accentSmall {{ border: 2px solid {t.accent}; }}
QPushButton#accentSmall:focus {{ border-color: {primary_ring}; padding: {bv}px {bh}px; }}

QToolButton#sectionToggle {{
    color: {t.text}; font-weight: 700; border: 2px solid transparent; border-radius: {m.control_radius}px;
    padding: 3px 12px 3px 6px;
}}
QToolButton#sectionToggle:hover {{ color: {t.accent}; background: {t.accent_soft}; }}
QToolButton#sectionToggle:checked {{ color: {t.accent}; }}
QToolButton#sectionToggle:focus {{ border-color: {ring}; }}

QLineEdit:hover, QComboBox:hover {{ border-color: {t.accent}; }}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{
    border: 3px solid {ring}; padding: {cv - 1}px {ch - 1}px;
}}
/* the Speed limit field is narrow: give its placeholder the room it needs */
QWidget#advancedPanel QLineEdit[placeholderText="No limit (e.g. 5M)"] {{ padding-left: 4px; padding-right: 4px; }}
QWidget#advancedPanel QLineEdit[placeholderText="No limit (e.g. 5M)"]:focus {{ padding-left: 3px; padding-right: 3px; }}
QComboBox:focus, QComboBox:on {{ padding-right: 33px; }}
QComboBox {{ padding-right: 34px; }}
QComboBox::drop-down {{ width: 32px; }}
QComboBox::down-arrow {{ image: url("{glyph('chevron', t.text)}"); width: 14px; height: 14px; }}
QComboBox::down-arrow:on {{ image: url("{glyph('chevron', t.accent)}"); }}
QComboBox QAbstractItemView {{ border: 2px solid {t.border_strong}; padding: 4px; }}
QComboBox QAbstractItemView::item {{ min-height: 40px; padding: 0 10px; }}

QLineEdit, QPlainTextEdit {{ placeholder-text-color: {t.muted}; }}
QPlainTextEdit {{ border-radius: {m.control_radius + 2}px; }}
QPlainTextEdit:focus {{ border: 3px solid {ring}; padding: 3px; }}
QPlainTextEdit#links {{ padding: 7px 6px; font-size: {m.font_size}pt; }}
QPlainTextEdit#links:focus {{ padding: 6px 5px; }}
QPlainTextEdit#log {{ background: {t.alt}; border: 2px solid {t.border}; }}

QCheckBox {{ spacing: 10px; padding: 2px 0; }}
QCheckBox::indicator {{ border-radius: 4px; border: 2px solid {t.border_strong}; }}
QCheckBox::indicator:hover {{ border-color: {t.accent}; background: {t.accent_soft}; }}
QCheckBox::indicator:focus {{ border: 3px solid {ring}; width: {m.indicator_size - 2}px; height: {m.indicator_size - 2}px; }}

QTreeWidget#queue {{
    background: {t.card}; border: 2px solid {t.border_strong}; border-radius: 4px;
}}
QTreeWidget#queue::item {{ border-bottom: 1px solid {t.border}; padding: 0 6px; }}
QTreeWidget#queue::item:hover {{ background: {t.alt}; }}
QHeaderView::section {{
    background: {t.alt}; color: {t.text}; font-weight: 700;
    border: none; border-bottom: 2px solid {t.border_strong}; padding: 7px 10px;
}}

QFrame#errorBanner {{ background: {soft_danger}; border: 2px solid {t.danger}; border-radius: 8px; }}

QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ border-radius: 5px; }}
QScrollBar::handle:hover {{ background: {t.text}; }}

QToolTip {{ border: 2px solid {t.border_strong}; padding: 8px 10px; font-size: {m.font_size}pt; }}
QMenu {{ border: 2px solid {t.border_strong}; padding: 6px; }}
QMenu::item {{ padding: 10px 30px 10px 18px; }}
QMenu::indicator {{ width: 16px; height: 16px; left: 7px; }}

QStatusBar {{ background: {t.alt}; color: {t.text}; border-top: 2px solid {t.border}; }}
QStatusBar QLabel {{ color: {t.text}; }}
QStatusBar QPushButton#flat {{ padding: 1px 10px; font-size: {m.font_size - 1}pt; }}
QStatusBar QLabel {{ font-size: {m.font_size - 1}pt; }}
"""


def after_refresh(w: MainWindow, t: Theme) -> None:
    """Icons drawn in code follow the variant's own colours."""
    w.settings_button.setIcon(glyph_icon("settings", QColor(ON_HEADER)))
    w.paste_btn.setIcon(glyph_icon("paste", t.color("accent")))
    w.download_btn.setIcon(glyph_icon("download", t.color("on_accent"), t.color("muted")))


VARIANT = Variant(
    key="large",
    name="Large & Clear",
    description="Big text, strong contrast and three numbered steps in one simple column.",
    light=LIGHT,
    dark=DARK,
    metrics=METRICS,
    build_layout=build,
    extra_qss=extra_qss,
    after_refresh=after_refresh,
)
