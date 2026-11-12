"""Classic: two rounded cards (New download, Downloads) on a soft grey page, blue accent.

This is the default look and the reference implementation for other variants.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QWidget

from .. import kit
from ..theme import Metrics, Theme, Variant

if TYPE_CHECKING:
    from ..main_window import MainWindow

LIGHT = Theme(
    dark=False,
    window="#f3f4f7",
    card="#ffffff",
    alt="#f7f8fa",
    hover="#eef1f6",
    border="#e1e4ea",
    border_strong="#cdd2da",
    text="#1d2129",
    muted="#6b7280",
    accent="#2f6fed",
    accent_hover="#2763dc",
    accent_pressed="#1f53bd",
    accent_disabled="#a9c0f5",
    accent_soft="#e8effe",
    selection="#dde8fd",
    track="#e6e9ef",
    success="#1e9a55",
    warning="#c47a0a",
    danger="#d93025",
    scroll="#c3c8d1",
)

DARK = Theme(
    dark=True,
    window="#1a1c20",
    card="#23262b",
    alt="#292c32",
    hover="#2d3138",
    border="#33373e",
    border_strong="#454a53",
    text="#e8eaed",
    muted="#9aa0aa",
    accent="#5b8cff",
    accent_hover="#6f9bff",
    accent_pressed="#4a79e8",
    accent_disabled="#344466",
    accent_soft="#26324a",
    selection="#2c3a57",
    track="#363a42",
    success="#4cc27e",
    warning="#f0b450",
    danger="#ff6b5f",
    scroll="#4a4f58",
)


def build(w: MainWindow, content: QWidget) -> None:
    root = kit.vbox(spacing=14, margins=(20, 16, 20, 6))
    content.setLayout(root)

    # Header: logo, title + tagline … Settings
    root.addLayout(kit.hbox(
        w.logo_label,
        kit.vbox(w.title_label, w.tagline_label, spacing=0),
        None,
        w.settings_button,
        spacing=12,
    ))

    # New download
    new = kit.card(content)
    lay = kit.vbox(spacing=12, margins=(20, 16, 20, 16))
    new.setLayout(lay)
    lay.addLayout(kit.hbox(kit.title("New download", new), None, w.clear_links_btn, w.paste_btn))
    w.url_edit.setMinimumHeight(84)
    w.url_edit.setMaximumHeight(130)
    lay.addWidget(w.url_edit)

    grid = QGridLayout()
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(8)
    grid.setColumnStretch(1, 1)
    preset_label = kit.field_label("Preset", new)
    preset_label.setContentsMargins(0, 8, 0, 0)  # line up with the combo, not its hint
    folder_label = kit.field_label("Save to", new)
    options_label = kit.field_label("Options", new)
    grid.addWidget(preset_label, 0, 0, Qt.AlignmentFlag.AlignTop)
    grid.addLayout(kit.vbox(w.preset_combo, w.preset_hint, spacing=4), 0, 1)
    grid.addWidget(folder_label, 1, 0)
    grid.addLayout(kit.hbox((w.folder_edit, 1), w.browse_btn, w.open_folder_btn), 1, 1)
    grid.addWidget(options_label, 2, 0)
    grid.addLayout(kit.hbox(w.playlist_check, w.subs_check, w.meta_check, None, spacing=22), 2, 1)
    lay.addLayout(grid)
    lay.addWidget(w.advanced_panel)
    lay.addWidget(kit.divider(new))
    w.download_btn.setMinimumWidth(190)
    lay.addLayout(kit.hbox(w.advanced_toggle, None, w.cancel_btn, w.download_btn, spacing=10))
    root.addWidget(new)

    # Downloads
    downloads = kit.card(content)
    dlay = kit.vbox(spacing=10, margins=(20, 14, 20, 12))
    downloads.setLayout(dlay)
    dlay.addLayout(kit.hbox(
        kit.title("Downloads", downloads), 10, w.summary_label, None, w.clear_finished_btn,
    ))
    dlay.addWidget(w.queue, 3)
    dlay.addWidget(w.banner)
    dlay.addLayout(kit.hbox(w.log_toggle, None, w.copy_log_btn, w.clear_log_btn))
    dlay.addWidget(w.log_view, 2)
    root.addWidget(downloads, 1)

    kit.align_labels([preset_label, folder_label, options_label, *w.advanced_panel.labels])


VARIANT = Variant(
    key="classic",
    name="Classic",
    description="Two rounded cards on a soft grey page with a blue accent.",
    light=LIGHT,
    dark=DARK,
    metrics=Metrics(),
    build_layout=build,
)
