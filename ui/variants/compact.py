"""Compact: dense, edge-to-edge layout for power users and small laptop screens.

A thin title bar, a short "composer" band (link box, one row of choices ending in
Download, one row of options) and a downloads list that gets most of the window.
Slate greys with a teal accent, small radii and an 8 px rhythm.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QFont, QResizeEvent
from PySide6.QtWidgets import QBoxLayout, QLabel, QSizePolicy, QWidget

from .. import kit
from ..icon import glyph_icon
from ..theme import GlyphFile, Metrics, Theme, Variant, mix

if TYPE_CHECKING:
    from ..main_window import MainWindow

LIGHT = Theme(
    dark=False,
    window="#f2f4f7",
    card="#ffffff",
    alt="#f6f8fa",
    hover="#edf1f5",
    border="#e0e5eb",
    border_strong="#c8d0da",
    text="#1a222d",
    muted="#5d6878",
    accent="#0f7a71",
    accent_hover="#0c6c64",
    accent_pressed="#0a5c55",
    accent_disabled="#d9eae8",
    accent_soft="#e2f2f0",
    selection="#d6ebe8",
    track="#e2e7ed",
    success="#2b9a47",
    warning="#c27c0e",
    danger="#d23b31",
    scroll="#c3cbd5",
)

DARK = Theme(
    dark=True,
    window="#14181e",
    card="#1b2028",
    alt="#20262f",
    hover="#252c36",
    border="#2a313b",
    border_strong="#3a4350",
    text="#e2e7ee",
    muted="#8e99a8",
    accent="#2ab3a3",
    accent_hover="#3cc3b3",
    accent_pressed="#229a8c",
    accent_disabled="#1d3a3b",
    accent_soft="#173230",
    selection="#1c3936",
    track="#2c343f",
    success="#4fc27a",
    warning="#e7ad48",
    danger="#f0685e",
    scroll="#3d4653",
    on_accent="#06201d",
)

METRICS = Metrics(
    font_families=("Segoe UI Variable Text", "Segoe UI", "Noto Sans", "DejaVu Sans"),
    font_size=9.5,
    app_title_size=10.5,
    card_title_size=9.5,
    heading_weight=600,
    card_radius=4,
    control_radius=4,
    primary_radius=4,
    control_padding=(4, 8),
    button_padding=(4, 10),
    primary_padding=(4, 14),
    primary_font_size=9.5,
    indicator_size=14,
    scrollbar_width=10,
    row_extra=12,
)

ROW_H = 30  # height of every control in the composer rows
ACTION_W = 160  # Paste/Clear column = Download width, so they line up
PAD = 16  # horizontal page padding
_QT_MAX = 16777215  # QWIDGETSIZE_MAX


def _caption(text: str, parent: QWidget) -> QLabel:
    """Small letter-spaced section caption, e.g. DOWNLOADS."""
    label = QLabel(text, parent)
    label.setObjectName("cxCaption")
    font = QFont(label.font())
    font.setPointSizeF(METRICS.font_size - 1.5)
    font.setWeight(QFont.Weight.Bold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.9)
    label.setFont(font)
    return label


class _ChoiceBlock(QWidget):
    """Preset, folder and Download.

    Wide (the usual window): one row — preset (hint below it) | folder, Browse…, Open |
    Cancel all, Download. Narrow (small laptops): preset with its hint beside it on top,
    folder … Download below, so neither the preset name nor the path gets cut. In the
    narrow form the preset box is made exactly as wide as the folder field, so both rows
    share one column edge.
    """

    WIDE_AT = 980  # block width (px) from which one row fits a full preset name and path
    HYSTERESIS = 24  # a page scrollbar appearing must not flip the layout back and forth
    GAP = 8  # spacing inside a row
    GROUP_GAP = 16  # between the folder group and Cancel/Download

    def __init__(self, parent: QWidget, combo: QWidget, hint: QWidget, field: QWidget,
                 buttons: tuple[QWidget, ...], actions: tuple[QWidget, ...]) -> None:
        super().__init__(parent)
        self._combo = combo
        self._fixed = (*buttons, *actions)
        self._preset = kit.vbox((combo, 1), (hint, 1))
        self._rest = kit.hbox((field, 1), *buttons, spacing=self.GAP)
        self._rest.addSpacing(self.GROUP_GAP - self.GAP)
        for widget in actions:
            self._rest.addWidget(widget)
        self._rest.setAlignment(Qt.AlignmentFlag.AlignTop)  # line up with the combo, not its hint
        self._outer = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.addLayout(self._preset, 1)
        self._outer.addLayout(self._rest, 2)
        self._wide: bool | None = None
        self._set_wide(True)

    def _set_wide(self, wide: bool) -> None:
        if wide == self._wide:
            return
        self._wide = wide
        direction = QBoxLayout.Direction
        self._outer.setDirection(direction.LeftToRight if wide else direction.TopToBottom)
        self._outer.setSpacing(self.GROUP_GAP if wide else self.GAP)
        self._preset.setDirection(direction.TopToBottom if wide else direction.LeftToRight)
        self._preset.setSpacing(4 if wide else self.GAP)
        if wide:
            self._combo.setMinimumWidth(0)
            self._combo.setMaximumWidth(_QT_MAX)
        self._fit()

    def _fit(self) -> None:
        """Narrow form: preset box width = folder field width (the row below)."""
        if self._wide:
            return
        fixed = sum(max(b.sizeHint().width(), b.minimumWidth()) for b in self._fixed)
        fixed += self.GAP * len(self._fixed) + self.GROUP_GAP - self.GAP
        self._combo.setFixedWidth(max(self._combo.minimumSizeHint().width(), self.width() - fixed))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        width = event.size().width()
        threshold = self.WIDE_AT - self.HYSTERESIS if self._wide else self.WIDE_AT
        if self._wide == (width >= threshold):
            self._fit()
        else:
            self._set_wide(width >= threshold)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.LayoutRequest:  # e.g. "Download 12 links" got wider
            self._fit()
        return super().event(event)


def build(w: MainWindow, content: QWidget) -> None:
    root = kit.vbox(spacing=0)
    content.setLayout(root)

    # ── Title bar: logo, name … Settings
    bar = kit.panel(content, "cxBar")
    w.set_logo_size(20)
    w.settings_button.setIconSize(QSize(14, 14))
    w.settings_button.setFixedHeight(28)
    bar.setLayout(kit.hbox(
        w.logo_label, w.title_label, None, w.settings_button,
        spacing=8, margins=(PAD, 6, PAD, 6),
    ))
    root.addWidget(bar)

    # ── Composer: link box | Paste/Clear, choices + Download, options
    composer = kit.panel(content, "cxComposer")
    lay = kit.vbox(spacing=8, margins=(PAD, 12, PAD, 12))
    composer.setLayout(lay)

    w.url_edit.setFixedHeight(ROW_H * 2 + 8)
    for button in (w.paste_btn, w.clear_links_btn):
        button.setFixedSize(ACTION_W, ROW_H)
    w.paste_btn.setIconSize(QSize(14, 14))
    kit.set_role(w.paste_btn, "cxPaste")  # step 1 in teal outline, Download (step 3) in teal fill
    lay.addLayout(kit.hbox((w.url_edit, 1), kit.vbox(w.paste_btn, w.clear_links_btn, spacing=8), spacing=8))

    w.preset_combo.setMinimumContentsLength(14)
    for part in (w.preset_combo, w.folder_edit, w.browse_btn, w.open_folder_btn,
                 w.cancel_btn, w.download_btn):
        part.setFixedHeight(ROW_H)
    w.download_btn.setMinimumWidth(ACTION_W)
    w.download_btn.setIconSize(QSize(16, 16))
    lay.addWidget(_ChoiceBlock(
        composer, w.preset_combo, w.preset_hint, w.folder_edit,
        buttons=(w.browse_btn, w.open_folder_btn), actions=(w.cancel_btn, w.download_btn),
    ))

    # Options; "Advanced options" sits centred in the Paste/Download column.
    advanced = QWidget(composer)
    advanced.setFixedWidth(ACTION_W)
    advanced.setLayout(kit.hbox(None, w.advanced_toggle, None))
    lay.addLayout(kit.hbox(w.playlist_check, w.subs_check, w.meta_check, None, advanced, spacing=24))
    lay.addWidget(w.advanced_panel)
    kit.align_labels(w.advanced_panel.labels)
    root.addWidget(composer)

    # ── Downloads: caption row, the list (most of the window), error banner, log
    downloads = kit.panel(content, "cxList")
    dlay = kit.vbox(spacing=0)
    downloads.setLayout(dlay)
    dlay.addLayout(kit.hbox(
        _caption("DOWNLOADS", downloads), w.summary_label, None, w.clear_finished_btn,
        spacing=12, margins=(PAD, 8, PAD - 6, 6),
    ))
    # 6 px + the delegates' own 10 px inset puts titles on the same 16 px line as above.
    dlay.addLayout(kit.hbox(w.queue, margins=(6, 0, 6, 0)), 3)
    dlay.addLayout(kit.hbox(w.banner, margins=(PAD, 8, PAD, 8)))
    log_bar = kit.panel(downloads, "cxLogBar")
    log_bar.setLayout(kit.hbox(
        w.log_toggle, None, w.copy_log_btn, w.clear_log_btn,
        spacing=4, margins=(PAD - 4, 3, PAD - 6, 3),
    ))
    dlay.addWidget(log_bar)
    # Directly in the column so the window can give it stretch while it is open;
    # styled as an edge-to-edge console strip.
    dlay.addWidget(w.log_view)
    root.addWidget(downloads, 1)

    # The list and the log take whatever height is left (never their 192 px default
    # preference), so the page only scrolls when it truly cannot fit.
    for view in (w.queue, w.log_view):
        view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
    w.log_view.setMinimumHeight(96)
    for column, width in ((1, 170), (2, 128), (3, 84), (4, 56), (5, 118)):
        w.queue.setColumnWidth(column, width)


def _chrome(t: Theme) -> tuple[str, str, str]:
    """Title bar colours (background, text, muted): a deep slate strip in both schemes."""
    if t.dark:
        bg = mix(t.window, "#000000", 0.38)
        return bg, t.text, mix(bg, t.text, 0.62)
    bg = t.text
    return bg, t.card, mix(bg, t.card, 0.62)


def _disabled_text(t: Theme) -> str:
    """Label of the disabled Download button: teal on pale teal, readable in both schemes."""
    return mix(t.card, t.accent, 0.8)


def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:
    bar, bar_text, bar_muted = _chrome(t)
    return f"""
QFrame#cxBar {{ background: {bar}; border: none; }}
QFrame#cxBar QLabel#appTitle {{ color: {bar_text}; background: transparent; }}
QFrame#cxBar QToolButton#menuButton {{
    background: transparent; color: {bar_text};
    border: 1px solid {mix(bar, bar_text, 0.22)}; padding: 2px 24px 2px 8px;
}}
QFrame#cxBar QToolButton#menuButton:hover {{ background: {mix(bar, bar_text, 0.10)}; border-color: {mix(bar, bar_text, 0.35)}; }}
QFrame#cxBar QToolButton#menuButton:pressed {{ background: {mix(bar, bar_text, 0.16)}; }}
QFrame#cxBar QToolButton#menuButton::menu-indicator {{
    image: url("{glyph('chevron', bar_muted)}"); right: 8px; width: 9px; height: 9px;
}}

QFrame#cxComposer {{ background: {t.window}; border: none; border-bottom: 1px solid {t.border}; }}
QFrame#cxList {{ background: {t.card}; border: none; }}
QFrame#cxLogBar {{ background: {t.card}; border: none; border-top: 1px solid {t.border}; }}
QLabel#cxCaption {{ color: {t.muted}; }}

/* One control height (30 px) everywhere: min-height is the content box. */
QLineEdit, QComboBox, QPushButton {{ min-height: 20px; }}
QPushButton#primary, QPushButton#accentSmall {{ min-height: 22px; }}  /* borderless */
QPushButton#primary:disabled {{ background: {t.accent_disabled}; color: {_disabled_text(t)}; }}
QPushButton#flat {{ min-height: 16px; padding: 3px 8px; }}
QPushButton[role="cxPaste"] {{
    color: {t.accent}; font-weight: 600; border-color: {mix(t.border_strong, t.accent, 0.55)};
}}
QPushButton[role="cxPaste"]:hover {{ background: {t.accent_soft}; border-color: {t.accent}; }}
QPushButton[role="cxPaste"]:pressed {{ background: {mix(t.accent_soft, t.accent, 0.15)}; }}
QToolButton#sectionToggle {{ padding: 3px 0 3px 2px; }}

QPlainTextEdit#links {{ padding: 3px 4px; font-size: {m.font_size}pt; }}
QComboBox QAbstractItemView::item {{ min-height: 26px; }}
QMenu {{ padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 12px; border-radius: 3px; }}

QTreeWidget#queue::item {{ padding: 0 10px; }}
QHeaderView::section {{
    padding: 5px 10px; font-size: {m.font_size - 1}pt; background: {t.card};
}}
QFrame#cxList QPlainTextEdit#log {{
    background: {t.alt}; border: none; border-top: 1px solid {t.border}; border-radius: 0;
    padding: 4px 12px;
}}
QStatusBar {{ border-top: 1px solid {t.border}; }}
"""


def after_refresh(w: MainWindow, t: Theme) -> None:
    """Settings sits on the dark title bar; Paste and the disabled Download match their text."""
    w.settings_button.setIcon(glyph_icon("settings", QColor(_chrome(t)[1])))
    w.paste_btn.setIcon(glyph_icon("paste", t.color("accent")))
    w.download_btn.setIcon(glyph_icon("download", t.color("on_accent"), QColor(_disabled_text(t))))


VARIANT = Variant(
    key="compact",
    name="Compact",
    description="Dense and efficient: a slim toolbar, one row of choices and a big download list, "
                "in slate grey with a teal accent.",
    light=LIGHT,
    dark=DARK,
    metrics=METRICS,
    build_layout=build,
    extra_qss=extra_qss,
    after_refresh=after_refresh,
)
