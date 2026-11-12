"""Fluent: a Windows 11 Settings look.

Mica-grey page, the link box in its own card, then a stack of slim "setting rows" (icon,
title and one-line description on the left, the control on the right), a command bar with
the accent-filled Download button, and the downloads list in a card below. Small 4-6 px
radii, Segoe UI Variable, Windows accent blue (light #005fb8, dark #60cdff with dark text).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QBoxLayout, QLabel, QSizePolicy, QWidget

from .. import kit
from .. import theme as theme_module
from ..icon import glyph_pixmap
from ..theme import GlyphFile, Metrics, Theme, Variant, mix
from ..widgets import Card

if TYPE_CHECKING:
    from ..main_window import MainWindow

TEXT_FONT = ("Segoe UI Variable Text", "Segoe UI", "Noto Sans", "DejaVu Sans")
DISPLAY_FONT = ("Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI", "Noto Sans", "DejaVu Sans")
ICON_SIZE = 20  # row icons, like the Fluent glyphs in Windows Settings
WIDE_CONTROLS = 420  # max width of the preset / folder controls

LIGHT = Theme(
    dark=False,
    window="#f3f3f3",
    card="#fbfbfb",
    alt="#f5f5f5",
    hover="#f6f6f6",
    border="#e5e5e5",
    border_strong="#d1d1d1",
    text="#1b1b1b",
    muted="#5d5d5d",
    accent="#005fb8",
    accent_hover="#196fbf",  # Windows: accent at 90 % opacity
    accent_pressed="#327ec5",  # 80 %
    accent_disabled="#93b8de",
    accent_soft="#e6eef8",
    selection="#e8eef6",
    track="#d9d9d9",
    success="#0f7b0f",
    warning="#a66200",
    danger="#c42b1c",
    scroll="#a3a3a3",
    on_accent="#ffffff",
)

DARK = Theme(
    dark=True,
    window="#202020",
    card="#2b2b2b",
    alt="#262626",
    hover="#323232",
    border="#3a3a3a",
    border_strong="#4b4b4b",
    text="#ffffff",
    muted="#c5c5c5",
    accent="#60cdff",
    accent_hover="#5bbdea",
    accent_pressed="#55add5",
    accent_disabled="#3e6475",
    accent_soft="#2c3c45",
    selection="#33424b",
    track="#4a4a4a",
    success="#6ccb5f",
    warning="#ffc83d",
    danger="#ff99a4",
    scroll="#6e6e6e",
    on_accent="#000000",  # Windows draws dark text on the light-blue dark-mode accent
)


# ------------------------------------------------------------------ helpers


class _FollowVisibility(QObject):
    """Shows a decorative frame exactly while a toggled part (e.g. the advanced panel) is shown.

    Only observes the part: its visibility stays under the window's control. The filter is a
    child of the frame, so it disappears with the layout.
    """

    def __init__(self, part: QWidget, frame: QWidget) -> None:
        super().__init__(frame)
        self._frame = frame
        part.installEventFilter(self)
        frame.setVisible(not part.isHidden())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.ShowToParent:
            self._frame.setVisible(True)
        elif event.type() == QEvent.Type.HideToParent:
            self._frame.setVisible(False)
        return False


class _GrowCard(Card):
    """A card that asks only for its minimum height and grows with its stretch factor.

    The page is height-for-width (wrapped descriptions), so the scroll area sizes it by
    preferred heights: big default hints of the list / link box would make it scroll early.
    """

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = super().sizeHint()
        return QSize(hint.width(), self.minimumSizeHint().height())


def _grow_card(parent: QWidget) -> Card:
    card = _GrowCard(parent)
    card.setObjectName("card")
    return card


def _icon(parent: QWidget, glyph: str) -> QLabel:
    label = QLabel(parent)
    label.setObjectName("fluentIcon")
    label.setProperty("glyph", glyph)
    label.setFixedSize(ICON_SIZE, ICON_SIZE)
    label.setPixmap(glyph_pixmap(glyph, theme_module.current().color("text"), ICON_SIZE))
    return label


def _text_block(parent: QWidget, title: str, description: str | QLabel) -> QBoxLayout:
    heading = QLabel(title, parent)
    heading.setObjectName("fluentRowTitle")
    if isinstance(description, QLabel):
        desc = description
        kit.set_role(desc, "fluentDesc")
    else:
        desc = QLabel(description, parent)
        desc.setObjectName("fluentRowDesc")
        desc.setWordWrap(True)
    desc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return kit.vbox(heading, desc, spacing=1)


def _setting_row(parent: QWidget, glyph: str, title: str, description: str | QLabel,
                 controls: QWidget | QBoxLayout, wide: bool = False) -> QWidget:
    """One Windows-Settings card: icon · title/description … control(s) on the right.

    ``wide`` rows give their controls a fixed share of the width (capped), so the preset
    dropdown and the folder field line up with each other.
    """
    row = kit.card(parent)
    lay = kit.hbox(spacing=16, margins=(16, 7, 16, 7))
    row.setLayout(lay)
    lay.addWidget(_icon(row, glyph), 0, Qt.AlignmentFlag.AlignVCenter)
    lay.addLayout(_text_block(row, title, description), 8 if wide else 1)
    if isinstance(controls, QBoxLayout):
        holder = QWidget(row)
        holder.setLayout(controls)
        controls = holder
    if wide:
        controls.setMaximumWidth(WIDE_CONTROLS)
    lay.addWidget(controls, 10 if wide else 0, Qt.AlignmentFlag.AlignVCenter)
    return row


# ------------------------------------------------------------------- layout


def build(w: MainWindow, content: QWidget) -> None:
    root = kit.vbox(spacing=0, margins=(24, 10, 24, 8))
    content.setLayout(root)

    # Header: app icon, title and the one-line how-to, Settings on the right.
    w.set_logo_size(28)
    w.tagline_label.setContentsMargins(0, 0, 0, 4)  # sit on the title's baseline
    header = kit.hbox(w.logo_label, 12, w.title_label, 14, w.tagline_label, None, w.settings_button, spacing=0)
    for part in (w.logo_label, w.title_label, w.tagline_label):
        header.setAlignment(part, Qt.AlignmentFlag.AlignBottom)
    root.addLayout(header)
    root.addSpacing(10)

    # 1. Links — their own card, Paste / Clear beside the box.
    links = _grow_card(content)
    llay = kit.hbox(spacing=10, margins=(10, 10, 10, 10))
    links.setLayout(llay)
    w.url_edit.setMinimumHeight(56)
    w.url_edit.setMaximumHeight(96)
    links.setMaximumHeight(96 + 2 * 10 + 2)  # grows with the window only as far as the box does
    w.paste_btn.setMinimumWidth(96)
    w.clear_links_btn.setMinimumWidth(96)
    llay.addWidget(w.url_edit, 1)
    llay.addLayout(kit.vbox(w.paste_btn, w.clear_links_btn, None, spacing=2))
    root.addWidget(links, 1)
    root.addSpacing(8)

    # 2. Settings — a stack of setting cards, 4 px apart like Windows Settings.
    stack = kit.vbox(spacing=4)
    w.preset_combo.setMinimumWidth(220)
    w.preset_combo.setMinimumContentsLength(18)
    stack.addWidget(_setting_row(content, "video", "Preset", w.preset_hint, w.preset_combo, wide=True))

    w.folder_edit.setMinimumWidth(150)
    folder_controls = kit.hbox((w.folder_edit, 1), w.browse_btn, w.open_folder_btn, spacing=8)
    stack.addWidget(_setting_row(content, "folder", "Save to", "Where your files are saved",
                                 folder_controls, wide=True))

    checks = kit.hbox(w.playlist_check, w.subs_check, w.meta_check, spacing=20)
    stack.addWidget(_setting_row(content, "check", "Options", "Extras for every download", checks))

    # Advanced options: an extra card that appears in the stack while the panel is open.
    advanced = kit.card(content)
    alay = kit.vbox(spacing=4, margins=(16, 10, 16, 12))
    advanced.setLayout(alay)
    alay.addLayout(kit.hbox(
        _icon(advanced, "settings"),
        (_text_block(advanced, "Advanced options", "Cookies, file names, speed limit, your own presets"), 1),
        spacing=16,
    ))
    alay.addLayout(kit.hbox(ICON_SIZE + 16, (w.advanced_panel, 1), spacing=0))
    _FollowVisibility(w.advanced_panel, advanced)
    stack.addWidget(advanced)
    root.addLayout(stack)
    root.addSpacing(10)

    # 3. Command bar: Advanced on the left, the accent Download button on the right.
    w.download_btn.setMinimumWidth(200)
    root.addLayout(kit.hbox(w.advanced_toggle, None, w.cancel_btn, w.download_btn, spacing=8))
    root.addSpacing(12)

    # 4. Downloads: section header with the list commands, the list in a card.
    section = QLabel("Downloads", content)
    section.setObjectName("fluentSection")
    root.addLayout(kit.hbox(
        section, 12, w.summary_label, None, w.clear_finished_btn, 2, w.log_toggle, spacing=0,
    ))
    root.addSpacing(6)
    queue_card = _grow_card(content)
    qlay = kit.vbox(margins=(4, 2, 4, 4))
    queue_card.setLayout(qlay)
    qlay.addWidget(w.queue)
    for column, width in ((1, 160), (2, 140), (3, 88), (4, 58), (5, 128)):
        w.queue.setColumnWidth(column, width)
    # The error banner sits above the list like a Windows InfoBar (visible even on short
    # windows); the log opens below it. Hidden parts take no room.
    downloads = kit.vbox(spacing=8)
    downloads.addWidget(w.banner)
    downloads.addWidget(queue_card, 3)
    downloads.addWidget(w.log_view, 2)
    downloads.addLayout(kit.hbox(None, w.copy_log_btn, w.clear_log_btn, spacing=4))
    root.addLayout(downloads, 3)


def after_refresh(w: MainWindow, t: Theme) -> None:
    """Row icons are pixmaps: recolour them when light/dark changes."""
    color = QColor(t.text)
    for label in w.findChildren(QLabel, "fluentIcon"):
        glyph = label.property("glyph")
        if glyph:
            label.setPixmap(glyph_pixmap(str(glyph), color, ICON_SIZE))


# --------------------------------------------------------------- stylesheet


def _tokens(t: Theme) -> dict[str, str]:
    """Windows 11 control colours derived for this scheme."""
    if t.dark:
        return {
            "stroke": "#1c1c1c",  # card outline: darker than the card, as in Windows
            "ctrl": "#373737", "ctrl_hover": "#3d3d3d", "ctrl_pressed": "#323232",
            "ctrl_border": "#434343", "ctrl_bottom": "#3a3a3a",
            "input": "#333333", "input_hover": "#383838", "input_focus": "#1f1f1f",
            "input_line": "#9a9a9a",
            "subtle_hover": "#363636", "subtle_pressed": "#313131",
            "check_border": "#9e9e9e", "check_fill": "#262626", "check_hover": "#303030",
            "disabled_text": "#787878",
            "flyout": "#2c2c2c",
            "critical_bg": "#442726",  # Windows InfoBar (error) background
            "accent_top": mix(t.accent, "#ffffff", 0.10),
            "accent_bottom": mix(t.accent, "#000000", 0.18),
        }
    return {
        "stroke": "#e5e5e5",
        "ctrl": "#fdfdfd", "ctrl_hover": "#f7f7f7", "ctrl_pressed": "#f3f3f3",
        "ctrl_border": "#e5e5e5", "ctrl_bottom": "#cccccc",
        "input": "#ffffff", "input_hover": "#f9f9f9", "input_focus": "#ffffff",
        "input_line": "#8a8a8a",
        "subtle_hover": "#efefef", "subtle_pressed": "#eaeaea",
        "check_border": "#8b8b8b", "check_fill": "#f6f6f6", "check_hover": "#eeeeee",
        "disabled_text": "#a3a3a3",
        "flyout": "#f9f9f9",
        "critical_bg": "#fde7e9",
        "accent_top": mix(t.accent, "#ffffff", 0.10),
        "accent_bottom": mix(t.accent, "#000000", 0.40),
    }


def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:
    k = _tokens(t)
    cv, ch = m.control_padding
    bv, bh = m.button_padding
    pv, ph = m.primary_padding
    return f"""
QFrame#card {{ background: {t.card}; border: 1px solid {k['stroke']}; border-radius: {m.card_radius}px; }}
QLabel#appTagline {{ color: {t.muted}; }}
QLabel#fluentRowTitle {{ font-size: 10.5pt; color: {t.text}; }}
QLabel#fluentRowDesc, QLabel[role="fluentDesc"] {{ font-size: 9pt; color: {t.muted}; }}
QLabel#fluentSection {{ font-size: 11pt; font-weight: 600; color: {t.text}; }}

QPushButton {{
    background: {k['ctrl']}; color: {t.text};
    border: 1px solid {k['ctrl_border']}; border-bottom-color: {k['ctrl_bottom']};
    border-radius: {m.control_radius}px; padding: {bv}px {bh}px; min-height: 20px;
}}
QPushButton:hover {{ background: {k['ctrl_hover']}; }}
QPushButton:pressed {{ background: {k['ctrl_pressed']}; color: {t.muted}; border-bottom-color: {k['ctrl_border']}; }}
QPushButton:disabled {{ background: {k['ctrl']}; color: {k['disabled_text']}; border-color: {k['ctrl_border']}; }}
QPushButton:focus {{ border-color: {t.accent}; }}

QPushButton#primary {{
    background: {t.accent}; color: {t.on_accent};
    border: 1px solid {k['accent_top']}; border-bottom-color: {k['accent_bottom']};
    border-radius: {m.primary_radius}px; padding: {pv}px {ph}px;
    font-weight: 600; font-size: {m.primary_font_size}pt;
}}
QPushButton#primary:hover {{ background: {t.accent_hover}; }}
QPushButton#primary:pressed {{ background: {t.accent_pressed}; border-color: {t.accent_pressed}; }}
QPushButton#primary:disabled {{
    background: {t.accent_disabled}; border-color: {t.accent_disabled};
    color: {mix(t.accent_disabled, t.on_accent, 0.88)};
}}
QPushButton#secondary {{ padding: {pv}px {max(ph - 8, 12)}px; font-size: {m.primary_font_size}pt; }}

QPushButton#flat {{
    background: transparent; border: 1px solid transparent; color: {t.text};
    padding: {bv}px 10px;
}}
QPushButton#flat:hover {{ background: {k['subtle_hover']}; color: {t.text}; }}
QPushButton#flat:pressed {{ background: {k['subtle_pressed']}; color: {t.muted}; }}
QPushButton#flat:disabled {{ background: transparent; color: {k['disabled_text']}; }}

QToolButton#menuButton {{
    background: {k['ctrl']}; color: {t.text};
    border: 1px solid {k['ctrl_border']}; border-bottom-color: {k['ctrl_bottom']};
    border-radius: {m.control_radius}px; padding: {bv}px 30px {bv}px 10px;
}}
QToolButton#menuButton:hover {{ background: {k['ctrl_hover']}; }}
QToolButton#sectionToggle {{
    background: transparent; border: none; border-radius: {m.control_radius}px;
    color: {t.text}; font-weight: normal; padding: 5px 10px 5px 6px;
}}
QToolButton#sectionToggle:hover {{ background: {k['subtle_hover']}; color: {t.text}; }}
QToolButton#sectionToggle:checked {{ color: {t.text}; }}
QFrame#card QLabel#fieldLabel {{ color: {t.text}; font-weight: normal; }}

QLineEdit {{
    background: {k['input']}; color: {t.text};
    border: 1px solid {k['ctrl_border']}; border-bottom-color: {k['input_line']};
    border-radius: {m.control_radius}px; padding: {cv}px {ch}px {cv + 1}px {ch}px;
}}
QLineEdit:hover {{ background: {k['input_hover']}; border-color: {k['ctrl_border']}; border-bottom-color: {k['input_line']}; }}
QLineEdit:focus {{
    background: {k['input_focus']}; border-color: {k['ctrl_border']};
    border-bottom: 2px solid {t.accent}; padding-bottom: {cv}px;
}}
QLineEdit:disabled {{ background: {k['ctrl']}; color: {k['disabled_text']}; border-bottom-color: {k['ctrl_border']}; }}

QComboBox {{
    background: {k['ctrl']}; color: {t.text};
    border: 1px solid {k['ctrl_border']}; border-bottom-color: {k['ctrl_bottom']};
    border-radius: {m.control_radius}px; padding: {cv}px 30px {cv}px {ch}px;
}}
QComboBox:hover {{ background: {k['ctrl_hover']}; border-color: {k['ctrl_border']}; border-bottom-color: {k['ctrl_bottom']}; }}
QComboBox:focus, QComboBox:on {{ border-color: {k['ctrl_border']}; border-bottom-color: {t.accent}; }}
QComboBox QAbstractItemView {{
    background: {k['flyout']}; border: 1px solid {k['stroke']}; padding: 4px;
    selection-background-color: {k['subtle_hover']}; selection-color: {t.text};
}}
QComboBox QAbstractItemView::item {{ border-radius: 4px; }}
QComboBox QAbstractItemView::item:selected {{ background: {k['subtle_hover']}; border-left: 3px solid {t.accent}; }}

QPlainTextEdit#links {{
    background: {k['input']}; border: 1px solid {k['ctrl_border']}; border-bottom-color: {k['input_line']};
    border-radius: {m.control_radius}px; padding: 5px 4px 6px 4px;
}}
QPlainTextEdit#links:focus {{
    background: {k['input_focus']}; border-bottom: 2px solid {t.accent}; padding-bottom: 5px;
}}
QPlainTextEdit#links[dragActive="true"] {{ border: 2px dashed {t.accent}; background: {t.accent_soft}; }}
QPlainTextEdit#log {{
    background: {t.alt}; border: 1px solid {k['stroke']}; border-radius: {m.card_radius}px;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: {m.indicator_size}px; height: {m.indicator_size}px; border-radius: 4px;
    border: 1px solid {k['check_border']}; background: {k['check_fill']};
}}
QCheckBox::indicator:hover {{ background: {k['check_hover']}; border-color: {k['check_border']}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
QCheckBox::indicator:checked:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}

QTreeWidget#queue {{ background: {t.card}; }}
QTreeWidget#queue::item:selected:first {{ border-left: 3px solid {t.accent}; }}
QHeaderView::section {{
    background: {t.card}; color: {t.muted}; font-weight: normal;
    border: none; border-bottom: 1px solid {t.border}; padding: 7px 8px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ border-radius: 3px; }}

QMenu {{ background: {k['flyout']}; border: 1px solid {k['stroke']}; padding: 4px; }}
QMenu::item {{ padding: 7px 28px 7px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {k['subtle_hover']}; }}
QMenu::separator {{ background: {t.border}; }}

QFrame#errorBanner {{
    background: {k['critical_bg']}; border: 1px solid {mix(k['critical_bg'], t.danger, 0.22)};
    border-radius: {m.control_radius}px;
}}
"""


VARIANT = Variant(
    key="fluent",
    name="Fluent",
    description="Windows 11 Settings look: a stack of slim setting cards on Mica grey with an accent command bar.",
    light=LIGHT,
    dark=DARK,
    metrics=Metrics(
        font_families=TEXT_FONT,
        font_size=10.0,
        heading_families=DISPLAY_FONT,
        app_title_size=17.0,
        card_title_size=11.0,
        heading_weight=600,
        card_radius=6,
        control_radius=4,
        primary_radius=4,
        control_padding=(5, 10),
        button_padding=(5, 12),
        primary_padding=(9, 24),
        primary_font_size=10.5,
        border_width=1,
        indicator_size=18,
        scrollbar_width=10,
        row_extra=22,
    ),
    build_layout=build,
    extra_qss=extra_qss,
    after_refresh=after_refresh,
)
