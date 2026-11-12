"""Weir's design ("Studio"): a pro-tool look with a modern finish.

Numbered monospace sections, a terminal-style prompt, an inspector on the right and the
queue as a full-width monitor; graphite + lime in dark mode, white + lime in light mode.
It looks technical at first glance, and the numbers 01 → 04 spell out the path.

The window's controls ("parts") are created by MainWindow; ``build`` only arranges them.
Every part in main_window.REQUIRED_PARTS must be placed. Check the result with
``python tools/screenshots.py --check``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QWidget

from core import APP_VERSION

from . import kit
from .theme import Design, GlyphFile, Metrics, Theme, css_families

if TYPE_CHECKING:
    from .main_window import MainWindow

MONO: tuple[str, ...] = ("JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Liberation Mono")
SANS: tuple[str, ...] = ("Inter", "Segoe UI Variable Text", "Segoe UI", "Noto Sans", "DejaVu Sans")

DARK = Theme(
    dark=True,
    window="#0e1115",
    card="#151920",
    alt="#1a1f27",
    hover="#1f252e",
    border="#242a33",
    border_strong="#343c47",
    text="#e6e9ee",
    muted="#8a93a2",
    accent="#b6f04a",
    accent_hover="#c6fb63",
    accent_pressed="#a0d936",
    accent_disabled="#33401f",
    accent_soft="#1d2614",
    selection="#232e1c",
    track="#252c35",
    success="#5bd48a",
    warning="#f2b84b",
    danger="#ff6b6b",
    scroll="#38414c",
    on_accent="#0e1115",
)

LIGHT = Theme(
    dark=False,
    window="#edf0f3",
    card="#ffffff",
    alt="#f5f7f9",
    hover="#eef1f4",
    border="#dce1e7",
    border_strong="#c3cad3",
    text="#141920",
    muted="#5a6473",
    accent="#9be02a",
    accent_hover="#a9ec3d",
    accent_pressed="#88c91f",
    accent_disabled="#d6ecb2",
    accent_soft="#f1fadf",
    selection="#e8f5d2",
    track="#e3e8ee",
    success="#1f9a55",
    warning="#b8730a",
    danger="#d93025",
    scroll="#c3cad3",
    on_accent="#10150c",
    accent_text="#3f6f00",  # lime is too light to read as text on white
)

# Lime is too light for text on white: use a deep olive for accent-coloured text in light mode.
ACCENT_TEXT = {False: "#3f6f00", True: "#b6f04a"}

METRICS = Metrics(
    font_families=SANS,
    font_size=9.5,
    heading_families=MONO,
    mono_families=MONO,
    app_title_size=12.5,
    card_title_size=9.0,
    heading_weight=700,
    card_radius=8,
    control_radius=5,
    primary_radius=6,
    control_padding=(5, 8),
    button_padding=(5, 12),
    primary_padding=(10, 22),
    primary_font_size=10.0,
    indicator_size=14,
    scrollbar_width=9,
    row_extra=16,
)


def _mono_label(text: str, parent: QWidget, name: str, spacing: float = 0.0) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName(name)
    font = QFont(label.font())
    font.setFamilies(list(MONO))
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    label.setFont(font)
    return label


def _section(number: str, name: str, parent: QWidget, *trailing: kit.Item) -> kit.QHBoxLayout:
    """`01  SOURCE ────────  [trailing widgets]`"""
    rule = QFrame(parent)
    rule.setObjectName("rule")
    rule.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return kit.hbox(
        _mono_label(number, parent, "secNum", 0.5),
        _mono_label(name.upper(), parent, "secName", 1.6),
        (rule, 1),
        *trailing,
        spacing=8,
    )


def _panel(parent: QWidget, name: str = "panel") -> QFrame:
    return kit.panel(parent, name)


def _keys(parent: QWidget) -> kit.QHBoxLayout:
    """Keyboard legend drawn as key caps: [Ctrl+V] paste  [Ctrl+Enter] run  [Del] remove."""
    items: list[kit.Item] = []
    for key, action in (("Ctrl+V", "paste"), ("Ctrl+Enter", "run"), ("Del", "remove")):
        items += [_mono_label(key, parent, "kbd"), _mono_label(action, parent, "keys"), 10]
    return kit.hbox(*items[:-1], spacing=5)


def build(w: MainWindow, content: QWidget) -> None:
    root = kit.vbox(spacing=12, margins=(16, 12, 16, 8))
    content.setLayout(root)

    # Top bar: identity, version chip, keyboard legend, settings
    w.set_logo_size(24)
    chip = _mono_label(f"v{APP_VERSION}", content, "chip")
    chip.setToolTip("A simple window for yt-dlp")
    root.addLayout(kit.hbox(
        w.logo_label, w.title_label, 6, chip, None, _keys(content), 16, w.settings_button, spacing=10,
    ))

    # Top row: 01 Source (left, wide)  |  inspector 02 Format · 03 Output · 04 Run (right)
    source = _panel(content)
    slay = kit.vbox(spacing=10, margins=(14, 12, 14, 12))
    source.setLayout(slay)
    slay.addLayout(_section("01", "Source", source, w.clear_links_btn, w.paste_btn))
    prompt = _mono_label("›", source, "prompt")
    prompt.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
    prompt.setFixedWidth(18)
    w.url_edit.setMinimumHeight(92)
    slay.addLayout(kit.hbox(prompt, (w.url_edit, 1), spacing=6), 1)
    hint = _mono_label("YouTube, Vimeo, SoundCloud, Twitch… and 1,800+ other sites", source, "hint")
    hint.setWordWrap(True)
    slay.addLayout(kit.hbox(24, (hint, 1)))
    slay.addLayout(kit.hbox(18, w.advanced_toggle, None))
    slay.addWidget(w.advanced_panel)

    inspector = _panel(content, "inspector")
    inspector.setFixedWidth(318)
    ilay = kit.vbox(spacing=8, margins=(14, 12, 14, 14))
    inspector.setLayout(ilay)
    w.preset_combo.setMinimumContentsLength(18)
    ilay.addLayout(_section("02", "Format", inspector))
    ilay.addWidget(w.preset_combo)
    ilay.addWidget(w.preset_hint)
    ilay.addSpacing(6)
    ilay.addLayout(_section("03", "Output", inspector))
    ilay.addWidget(w.folder_edit)
    ilay.addLayout(kit.hbox((w.browse_btn, 1), (w.open_folder_btn, 1), spacing=6))
    ilay.addSpacing(2)
    for check in (w.playlist_check, w.subs_check, w.meta_check):
        ilay.addWidget(check)
    ilay.addSpacing(6)
    ilay.addLayout(_section("04", "Run", inspector))
    w.download_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    ilay.addWidget(w.download_btn)
    ilay.addWidget(w.cancel_btn)
    ilay.addStretch(1)

    root.addLayout(kit.hbox((source, 1), inspector, spacing=12))

    # Queue monitor (full width)
    monitor = _panel(content)
    mlay = kit.vbox(spacing=8, margins=(14, 12, 14, 10))
    monitor.setLayout(mlay)
    mlay.addLayout(_section("●", "Queue", monitor, w.summary_label, 8, w.clear_finished_btn))
    for column, width in ((1, 150), (2, 140), (3, 86), (4, 58), (5, 124)):
        w.queue.setColumnWidth(column, width)
    mlay.addWidget(w.queue, 3)
    mlay.addWidget(w.banner)
    mlay.addLayout(kit.hbox(w.log_toggle, None, w.copy_log_btn, w.clear_log_btn))
    mlay.addWidget(w.log_view, 2)
    root.addWidget(monitor, 1)


def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:
    mono = css_families(MONO)
    accent_text = ACCENT_TEXT[t.dark]
    return f"""
QFrame#panel, QFrame#inspector {{
    background: {t.card}; border: 1px solid {t.border}; border-radius: {m.card_radius}px;
}}
QFrame#inspector {{ background: {t.alt}; }}
QFrame#panel QLabel, QFrame#inspector QLabel {{ background: transparent; }}
QFrame#rule {{ background: {t.border}; min-height: 1px; max-height: 1px; border: none; }}

QLabel#secNum {{ color: {accent_text}; font-family: {mono}; font-size: 8.5pt; font-weight: 700; }}
QLabel#secName {{ color: {t.muted}; font-family: {mono}; font-size: 8.5pt; font-weight: 600; }}
QLabel#prompt {{ color: {accent_text}; font-family: {mono}; font-size: 16pt; font-weight: 700; padding-top: 2px; }}
QLabel#hint, QLabel#keys {{ color: {t.muted}; font-family: {mono}; font-size: 8pt; }}
QLabel#kbd {{
    color: {t.text}; font-family: {mono}; font-size: 7.5pt; font-weight: 600;
    background: {t.card}; border: 1px solid {t.border_strong}; border-bottom-width: 2px;
    border-radius: 4px; padding: 1px 5px;
}}
QPushButton, QToolButton#menuButton {{ font-family: {mono}; font-size: 9pt; }}
QLabel#chip {{
    color: {t.muted}; font-family: {mono}; font-size: 8pt;
    border: 1px solid {t.border_strong}; border-radius: 4px; padding: 2px 7px;
}}
QLabel#appTitle {{ font-family: {mono}; }}

QPlainTextEdit#links {{
    font-family: {mono}; font-size: 9.5pt; background: {t.alt};
    border: 1px solid {t.border_strong}; border-radius: {m.control_radius}px; padding: 6px 6px;
}}
QPlainTextEdit#links:focus {{ border-color: {t.accent}; background: {t.card}; }}
QPlainTextEdit#log {{ font-family: {mono}; }}

QPushButton#primary {{ font-family: {mono}; font-weight: 700; padding: 11px 18px; }}
QPushButton#secondary {{
    font-family: {mono}; font-size: 9pt; padding: 6px 12px; background: transparent;
    border: 1px solid {t.border_strong};
}}
QPushButton#secondary:hover {{ border-color: {t.danger}; color: {t.danger}; background: transparent; }}
QPushButton#secondary:disabled {{ border-color: {t.border}; color: {t.border_strong}; }}
QPushButton#flat:hover {{ color: {accent_text}; }}
QToolButton#sectionToggle {{ font-family: {mono}; font-size: 8.5pt; }}
QToolButton#sectionToggle:hover {{ color: {accent_text}; }}
QLabel#fieldLabel {{ font-family: {mono}; font-size: 8.5pt; }}

QHeaderView::section {{ font-family: {mono}; font-size: 8.5pt; background: {t.card}; }}
QTreeWidget#queue::item:selected {{ background: {t.selection}; }}
QCheckBox::indicator:checked {{ image: url("{glyph('check', t.on_accent)}"); }}
"""


DESIGN = Design(
    light=LIGHT,
    dark=DARK,
    metrics=METRICS,
    build_layout=build,
    extra_qss=extra_qss,
)
