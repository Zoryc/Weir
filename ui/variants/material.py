"""Material: an indigo app bar across the top, elevated white cards, outlined fields and chips.

Inspired by Google's Material Design: a full-width coloured top app bar (logo, title,
tagline, Settings), cards that sit slightly above a grey page (a darker bottom edge stands
in for the shadow), outlined text fields whose label sits in a notch of the border, the
three options as filter chips, pill-shaped buttons and an "extended FAB" Download button.
Advanced options open on a tinted inset inside the card; the Downloads card takes all
spare height, the link box grows a little with the window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QColor, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QApplication, QBoxLayout, QLabel, QLayout, QSizePolicy, QTreeWidget, QWidget

from .. import kit
from ..icon import glyph_icon
from ..theme import GlyphFile, Metrics, Theme, Variant, mix
from ..widgets import repolish

if TYPE_CHECKING:
    from ..main_window import MainWindow

LIGHT = Theme(
    dark=False,
    window="#eef0f6",
    card="#ffffff",
    alt="#f6f7fb",
    hover="#eef0f8",
    border="#e1e4ec",
    border_strong="#b3b8c6",
    text="#1b1c22",
    muted="#5d6274",
    accent="#3949ab",
    accent_hover="#32409d",
    accent_pressed="#283593",
    accent_disabled="#c5cae9",
    accent_soft="#e8eaf6",
    selection="#e0e4f7",
    track="#e2e5f0",
    success="#2e7d32",
    warning="#b26a00",
    danger="#c62828",
    scroll="#c2c6d2",
)

DARK = Theme(
    dark=True,
    window="#121318",
    card="#1c1e26",
    alt="#22252e",
    hover="#272a35",
    border="#2a2d38",
    border_strong="#4a4e5e",
    text="#e3e4ea",
    muted="#9a9fb2",
    accent="#a4afff",
    accent_hover="#b4bdff",
    accent_pressed="#8f9bf2",
    accent_disabled="#353a5e",
    accent_soft="#262b4a",
    selection="#2b3154",
    track="#2e313d",
    success="#69c46d",
    warning="#ffb547",
    danger="#f2716b",
    scroll="#454959",
    on_accent="#10153a",
)


@dataclass(frozen=True)
class _Bar:
    """Colours of the top app bar (not part of Theme: the bar is always a deep indigo)."""

    bg: str
    fg: str
    muted: str
    badge: str  # round tile behind the app icon, so the blue icon stands out from the bar


_BAR_LIGHT = _Bar(bg="#3949ab", fg="#ffffff", muted="#c9cef0", badge="#ffffff")
_BAR_DARK = _Bar(bg="#283070", fg="#f1f2ff", muted="#b3baea", badge="#e4e7ff")


_LOGO_BADGE = 36  # px, round tile behind the app icon
# Link box height: 3 lines on a 620 px tall window, growing with the window up to ~5.5 lines.
_LINKS_MIN, _LINKS_MAX = 72, 124
_LINKS_FROM_HEIGHT, _LINKS_GROWTH = 620, 0.14  # window height where growth starts; px per px

# Downloads list columns (Preset, Progress, Speed, ETA, Status); Title takes the rest.
_COLUMNS_WIDE = ((1, 150), (2, 136), (3, 88), (4, 58), (5, 124))
_COLUMNS_NARROW = ((1, 112), (2, 112), (3, 80), (4, 52), (5, 120))  # small windows: titles keep room
_COLUMNS_BREAKPOINT = 860  # list width in px


def _bar(t: Theme) -> _Bar:
    return _BAR_DARK if t.dark else _BAR_LIGHT


def _tonal_fg(t: Theme) -> str:
    """Text on accent_soft (tonal buttons, selected chips)."""
    return t.accent_hover if t.dark else t.accent_pressed


def _fab_disabled_fg(t: Theme) -> str:
    return mix(t.accent_disabled, t.text, 0.45) if t.dark else mix(t.accent_disabled, t.accent_pressed, 0.5)


class _Preferred(QWidget):
    """Holds one part and asks for ``height`` px (instead of the part's own, larger wish).

    The page lays out at preferred heights, so this decides what fits on small windows;
    the part still grows into any spare room (vertical policy Expanding).
    """

    def __init__(self, item: QWidget | QLayout, parent: QWidget, height: int) -> None:
        super().__init__(parent)
        self._height = height
        self.setLayout(kit.vbox(item, spacing=0))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(hint.width(), max(self._height, self.minimumSizeHint().height()))


class _QueueHolder(_Preferred):
    """Holds the downloads list and slims its side columns when the list is narrow.

    Widths change only when the breakpoint is crossed, so columns the user dragged stay put.
    """

    def __init__(self, queue: QTreeWidget, parent: QWidget, height: int) -> None:
        super().__init__(queue, parent, height)
        self._queue = queue
        self._wide: bool | None = None
        self._apply(False)

    def _apply(self, wide: bool) -> None:
        self._wide = wide
        for column, width in _COLUMNS_WIDE if wide else _COLUMNS_NARROW:
            self._queue.setColumnWidth(column, width)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        wide = event.size().width() >= _COLUMNS_BREAKPOINT
        if wide != self._wide:
            self._apply(wide)


class _Outlined(QWidget):
    """Material outlined field: the label sits in a notch of the field's top border and
    turns accent-coloured while the field has focus.

    ``height`` (optional) is the preferred height of the field; it then grows into spare room.
    """

    INSET = 8  # label x position; its 4px side padding lines the text up with the field's

    def __init__(self, text: str, item: QWidget | QLayout, parent: QWidget, height: int | None = None) -> None:
        super().__init__(parent)
        self._label = QLabel(text, self)
        self._label.setObjectName("mdNotch")
        self._label.ensurePolished()
        self.notch = (self._label.sizeHint().height() + 1) // 2
        self._height = height
        box = kit.vbox(item, spacing=0, margins=(0, self.notch, 0, 0))
        if isinstance(item, QWidget):  # a height-capped field stays under its label, never centred
            box.setAlignment(item, Qt.AlignmentFlag.AlignTop)
        self.setLayout(box)
        if height is not None:
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._place()
        app = QApplication.instance()
        if isinstance(app, QApplication):  # app-wide signal; disconnected when this widget dies
            app.focusChanged.connect(self._on_focus_changed)

    def _on_focus_changed(self, _old: QWidget | None, now: QWidget | None) -> None:
        try:
            focused = now is not None and self.isAncestorOf(now)
            if bool(self._label.property("focused")) != focused:
                self._label.setProperty("focused", focused)
                repolish(self._label)
        except RuntimeError:  # already deleted by a relayout
            pass

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if self._height is None:
            return hint
        return QSize(hint.width(), max(self.notch + self._height, self.minimumSizeHint().height()))

    def _place(self) -> None:
        self._label.adjustSize()
        self._label.move(self.INSET, 0)
        self._label.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._place()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._place()


class _LinksBox(_Outlined):
    """The Links field. Its preferred height follows the window's height.

    The page lays out at preferred heights (it scrolls rather than squeezing), so this is
    what makes the box 3 lines tall on small windows and roomier on big ones, while all
    remaining height still goes to the Downloads list.
    """

    def __init__(self, item: QWidget, parent: QWidget, window: QWidget) -> None:
        super().__init__("Links", item, parent, height=_LINKS_MIN)
        self._window = window
        self._window_height = -1
        window.installEventFilter(self)  # removed automatically when this box is deleted

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and watched is self._window:
            if self._window.height() != self._window_height:
                self.updateGeometry()
        return False

    def sizeHint(self) -> QSize:
        self._window_height = self._window.height()
        grow = round((self._window_height - _LINKS_FROM_HEIGHT) * _LINKS_GROWTH)
        self._height = max(_LINKS_MIN, min(_LINKS_MAX, _LINKS_MIN + grow))
        return super().sizeHint()


class _Reflow(QWidget):
    """Two columns side by side, stacked when narrower than ``breakpoint`` (smallest windows)."""

    def __init__(self, parent: QWidget, columns: tuple[tuple[QLayout, int], ...], breakpoint: int) -> None:
        super().__init__(parent)
        self._breakpoint = breakpoint
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(16)
        for column, stretch in columns:
            self._box.addLayout(column, stretch)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        stacked = event.size().width() < self._breakpoint
        direction = QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        if self._box.direction() != direction:
            self._box.setDirection(direction)
            self._box.setSpacing(10 if stacked else 16)


class _FollowShown(QObject):
    """Shows our decorative ``frame`` exactly while ``part`` is shown.

    The window itself shows/hides the part (the Advanced panel follows its toggle); this
    only mirrors that onto the tinted frame around it, so no empty frame is left behind.
    """

    def __init__(self, part: QWidget, frame: QWidget) -> None:
        super().__init__(frame)  # dies with the frame, which removes the filter
        self._part = part
        self._frame = frame
        frame.setVisible(not part.isHidden())
        part.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if watched is self._part and kind in (QEvent.Type.ShowToParent, QEvent.Type.HideToParent):
            try:
                self._frame.setVisible(kind == QEvent.Type.ShowToParent)
            except RuntimeError:  # frame already deleted by a relayout
                pass
        return False


def build(w: MainWindow, content: QWidget) -> None:
    root = kit.vbox(spacing=0)
    content.setLayout(root)

    # Top app bar: full width, accent colour; content lines up with the cards' edges.
    bar = kit.panel(content, "appBar")
    badge = kit.panel(bar, "appBarLogo")  # light round tile: the blue icon stands out on indigo
    badge.setFixedSize(_LOGO_BADGE, _LOGO_BADGE)
    w.set_logo_size(22)
    badge_box = kit.hbox()
    badge_box.addWidget(w.logo_label, 0, Qt.AlignmentFlag.AlignCenter)
    badge.setLayout(badge_box)
    w.tagline_label.setContentsMargins(0, 3, 0, 0)  # sit on the title's baseline
    bar.setLayout(kit.hbox(
        badge, 12, w.title_label, 16, w.tagline_label, None, w.settings_button,
        spacing=0, margins=(18, 7, 18, 7),
    ))
    root.addWidget(bar)
    shadow = kit.panel(content, "appBarShadow")
    shadow.setFixedHeight(5)
    root.addWidget(shadow)

    body = kit.vbox(spacing=10, margins=(18, 4, 18, 6))
    root.addLayout(body, 1)

    # New download: paste -> choose -> download. Takes only the height it needs; the
    # Downloads card below gets all spare room on big windows.
    new = kit.card(content, "mdCard")
    lay = kit.vbox(spacing=10, margins=(18, 12, 18, 12))
    new.setLayout(lay)

    w.url_edit.setMinimumHeight(_LINKS_MIN)
    w.url_edit.setMaximumHeight(_LINKS_MAX)
    links = _LinksBox(w.url_edit, new, w)
    links.setMaximumHeight(links.notch + _LINKS_MAX)
    kit.set_role(w.paste_btn, "tonal")
    w.paste_btn.setMinimumWidth(96)
    w.clear_links_btn.setMinimumWidth(96)
    side = kit.vbox(w.paste_btn, w.clear_links_btn, None, spacing=2, margins=(0, links.notch, 0, 0))
    lay.addLayout(kit.hbox((links, 1), side, spacing=12))

    w.preset_combo.setMinimumContentsLength(20)
    preset = _Outlined("Preset", w.preset_combo, new)
    w.preset_hint.setContentsMargins(12, 0, 0, 0)
    kit.set_role(w.preset_hint, "helper")
    kit.set_role(w.folder_edit, "path")  # icon-to-text gap like the preset combo's
    for button in (w.browse_btn, w.open_folder_btn):
        kit.set_role(button, "field")  # as tall as the field next to them
    folder = _Outlined(
        "Save to", kit.hbox((w.folder_edit, 1), w.browse_btn, w.open_folder_btn, spacing=8), new,
    )
    lay.addWidget(_Reflow(new, (
        (kit.vbox(preset, w.preset_hint, spacing=3), 5),
        (kit.vbox(folder, None, spacing=0), 6),  # the path needs the room
    ), breakpoint=700))

    for chip in (w.playlist_check, w.subs_check, w.meta_check):
        kit.set_role(chip, "chip")
    lay.addLayout(kit.hbox(w.playlist_check, w.subs_check, w.meta_check, None, spacing=8))

    # Advanced options sit on a tinted inset: a group of their own, so their side labels
    # don't read as a second style of the outlined fields above.
    inset = kit.panel(new, "mdInset")
    inset.setLayout(kit.vbox(w.advanced_panel))
    w.advanced_panel.setContentsMargins(16, 6, 16, 10)
    _FollowShown(w.advanced_panel, inset)
    lay.addWidget(inset)

    w.download_btn.setMinimumWidth(196)
    lay.addLayout(kit.hbox(w.advanced_toggle, None, w.cancel_btn, 4, w.download_btn, spacing=8))
    body.addWidget(new)

    # Downloads
    downloads = kit.card(content, "mdCard")
    dlay = kit.vbox(spacing=6, margins=(18, 10, 18, 10))
    downloads.setLayout(dlay)
    dlay.addLayout(kit.hbox(
        kit.title("Downloads", downloads), 12, w.summary_label, None,
        w.log_toggle, w.clear_finished_btn, spacing=4,
    ))
    w.queue.setMinimumHeight(84)  # column header + one download
    dlay.addWidget(_QueueHolder(w.queue, downloads, 84), 3)
    dlay.addWidget(w.banner)
    dlay.addLayout(kit.hbox(None, w.copy_log_btn, w.clear_log_btn, spacing=4))
    dlay.addWidget(w.log_view, 2)
    body.addWidget(downloads, 1)


def after_refresh(w: MainWindow, t: Theme) -> None:
    w.settings_button.setIcon(glyph_icon("settings", QColor(_bar(t).fg)))
    w.paste_btn.setIcon(glyph_icon("paste", QColor(_tonal_fg(t))))
    w.download_btn.setIcon(glyph_icon("download", t.color("on_accent"), QColor(_fab_disabled_fg(t))))


def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:
    bar = _bar(t)
    cv, ch = m.control_padding
    tonal = _tonal_fg(t)
    state = mix(t.card, t.accent, 0.08)  # Material "state layer" on hover
    state_pressed = mix(t.card, t.accent, 0.16)
    elevation = mix(t.window, "#000000", 0.55 if t.dark else 0.13)
    fab_edge = mix(t.accent, "#000000", 0.30 if not t.dark else 0.45)
    tip_bg, tip_fg = ("#e3e4ea", "#1b1c22") if t.dark else ("#3a3c45", "#ffffff")
    disabled_fg = mix(t.muted, t.card, 0.45)  # outlined and text buttons alike
    inset = mix(t.card, t.accent, 0.06 if t.dark else 0.035)  # "surface container" behind Advanced
    return f"""
QToolTip {{ background: {tip_bg}; color: {tip_fg}; border: none; padding: 5px 8px; }}

/* ---- top app bar ---- */
QFrame#appBar {{ background: {bar.bg}; border: none; }}
QFrame#appBar QLabel {{ background: transparent; }}
QFrame#appBar QLabel#appTitle {{ color: {bar.fg}; }}
QFrame#appBar QLabel#appTagline {{ color: {bar.muted}; }}
QFrame#appBarLogo {{ background: {bar.badge}; border: none; border-radius: {_LOGO_BADGE // 2}px; }}
QFrame#appBarShadow {{
    border: none;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {elevation}, stop:1 {t.window});
}}
QFrame#appBar QToolButton#menuButton {{
    background: transparent; color: {bar.fg}; font-weight: 600;
    border: 1px solid {mix(bar.bg, bar.fg, 0.38)}; border-radius: 17px;
    padding: 7px 32px 7px 14px;
}}
QFrame#appBar QToolButton#menuButton:hover {{ background: {mix(bar.bg, bar.fg, 0.12)}; }}
QFrame#appBar QToolButton#menuButton:pressed {{ background: {mix(bar.bg, bar.fg, 0.2)}; }}
QFrame#appBar QToolButton#menuButton::menu-indicator {{
    image: url("{glyph('chevron', bar.fg)}"); subcontrol-origin: padding;
    subcontrol-position: center right; right: 12px; width: 10px; height: 10px;
}}

/* ---- elevated cards ---- */
QFrame#mdCard {{
    background: {t.card}; border: 1px solid {t.border}; border-bottom: 2px solid {elevation};
    border-radius: {m.card_radius}px;
}}
QFrame#mdCard QLabel {{ background: transparent; }}
QFrame#mdCard QLabel#mdNotch {{
    background: {t.card}; color: {t.muted}; padding: 0 4px;
    font-size: {m.font_size - 1:.1f}pt; font-weight: 600;
}}
QFrame#mdCard QLabel#mdNotch[focused="true"] {{ color: {t.accent}; }}
QLabel[role="helper"] {{ font-size: {m.font_size - 1:.1f}pt; }}
QFrame#mdInset {{ background: {inset}; border: 1px solid {mix(inset, t.border, 0.6)}; border-radius: 10px; }}
QFrame#mdInset QLabel {{ background: transparent; }}

/* ---- outlined text fields: 2px accent outline when focused ---- */
QLineEdit:hover, QComboBox:hover {{ border-color: {mix(t.border_strong, t.text, 0.55)}; }}
QLineEdit:focus {{ border: 2px solid {t.accent}; padding: {cv - 1}px {ch - 1}px; }}
QComboBox:focus, QComboBox:on {{
    border: 2px solid {t.accent}; padding: {cv - 1}px 29px {cv - 1}px {ch - 1}px;
}}
QLineEdit[role="path"] {{ padding-left: 1px; }}  /* icon-to-text gap as in the preset combo */
QLineEdit[role="path"]:focus {{ padding-left: 0px; }}
QPlainTextEdit#links {{ border-radius: {m.control_radius}px; padding: 6px 5px; }}
QPlainTextEdit#links:focus {{ border: 2px solid {t.accent}; padding: 5px 4px; }}
QPlainTextEdit#links[dragActive="true"] {{
    border: 2px dashed {t.accent}; background: {t.accent_soft}; padding: 5px 4px;
}}

/* ---- pill buttons ---- */
QPushButton {{
    background: transparent; color: {t.accent}; font-weight: 600;
    border: 1px solid {t.border_strong}; border-radius: 16px; padding: 7px 16px;
}}
QPushButton:hover {{ background: {state}; }}
QPushButton:pressed {{ background: {state_pressed}; }}
QPushButton:disabled {{ color: {disabled_fg}; background: transparent; border-color: {t.border}; }}
QPushButton[role="field"] {{ padding: 8px 16px; border-radius: 18px; }}
QPushButton[role="tonal"] {{ background: {t.accent_soft}; color: {tonal}; border-color: {t.accent_soft}; }}
QPushButton[role="tonal"]:hover {{ background: {mix(t.accent_soft, t.accent, 0.12)}; }}
QPushButton[role="tonal"]:pressed {{ background: {mix(t.accent_soft, t.accent, 0.22)}; }}
QPushButton#flat {{
    background: transparent; border: none; color: {t.accent}; font-weight: 600;
    border-radius: 14px; padding: 5px 12px;
}}
QPushButton#flat:hover {{ background: {state}; color: {t.accent}; }}
QPushButton#flat:disabled {{ color: {disabled_fg}; background: transparent; }}
QStatusBar QPushButton#flat {{ color: {t.muted}; font-weight: normal; }}
QPushButton#accentSmall {{
    background: {t.accent}; color: {t.on_accent}; border: none; border-radius: 16px; padding: 7px 16px;
}}
QPushButton#secondary {{
    border-radius: 20px; padding: 10px 22px; font-size: {m.font_size + 0.5:.1f}pt;
}}

/* ---- extended FAB ---- */
QPushButton#primary {{
    background: {t.accent}; color: {t.on_accent}; border: none; border-bottom: 2px solid {fab_edge};
    border-radius: 16px; padding: 11px 26px 10px 22px; font-size: {m.primary_font_size}pt; font-weight: 600;
}}
QPushButton#primary:hover {{ background: {t.accent_hover}; }}
QPushButton#primary:pressed {{ background: {t.accent_pressed}; border-bottom-color: {t.accent_pressed}; }}
QPushButton#primary:disabled {{
    background: {t.accent_disabled}; color: {_fab_disabled_fg(t)}; border-bottom-color: {t.accent_disabled};
}}

/* ---- section toggles as text buttons ---- */
QToolButton#sectionToggle {{ border-radius: 14px; padding: 4px 12px 4px 8px; }}
QToolButton#sectionToggle:hover {{ background: {state}; }}

/* ---- options as filter chips ---- */
QCheckBox[role="chip"] {{
    background: {t.card}; color: {t.text};
    border: 1px solid {t.border_strong}; border-radius: 15px;
    padding: 5px 14px 5px 10px; spacing: 8px;
}}
QCheckBox[role="chip"]:hover {{ background: {state}; }}
QCheckBox[role="chip"]:checked {{
    background: {t.accent_soft}; color: {tonal}; border-color: {mix(t.accent_soft, t.accent, 0.35)};
}}

/* ---- downloads list ---- */
QHeaderView::section {{ padding: 7px 8px; }}
QTreeWidget#queue::item:hover {{ background: {t.hover}; }}
"""


VARIANT = Variant(
    key="material",
    name="Material",
    description="Material Design: an indigo app bar, elevated cards, outlined fields, chips and a big Download button.",
    light=LIGHT,
    dark=DARK,
    metrics=Metrics(
        font_families=("Roboto", "Segoe UI Variable Text", "Segoe UI", "Noto Sans", "DejaVu Sans"),
        font_size=10.0,
        heading_families=("Roboto", "Segoe UI Variable Display", "Segoe UI", "Noto Sans", "DejaVu Sans"),
        app_title_size=14.5,
        card_title_size=12.5,
        heading_weight=600,
        card_radius=12,
        control_radius=4,
        primary_radius=16,
        control_padding=(8, 12),
        button_padding=(7, 16),
        primary_padding=(11, 26),  # the FAB rule in extra_qss fine-tunes this
        primary_font_size=11.0,
        indicator_size=16,
        scrollbar_width=10,
        row_extra=20,
    ),
    build_layout=build,
    extra_qss=extra_qss,
    after_refresh=after_refresh,
)
