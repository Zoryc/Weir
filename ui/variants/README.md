# Writing a UI variant

A *variant* is one complete look of the main window: colours for light and dark mode,
shapes and typography, extra stylesheet rules, and a **layout function** that arranges
the window's controls. Users switch variants live under **Settings → Appearance**.

The controls themselves (“parts”) are created once by `MainWindow` and keep all their
behaviour. A variant only **places and styles** them; it never creates its own inputs or
connects signals.

`classic.py` is the reference implementation. Copy it as a starting point.

## The module

`ui/variants/<key>.py` must define `VARIANT`:

```python
from __future__ import annotations
from typing import TYPE_CHECKING
from PySide6.QtWidgets import QWidget
from .. import kit
from ..theme import GlyphFile, Metrics, Theme, Variant

if TYPE_CHECKING:
    from ..main_window import MainWindow

LIGHT = Theme(dark=False, window="#…", card="#…", …)   # every field, see ui/theme.py
DARK = Theme(dark=True, …)

def build(w: MainWindow, content: QWidget) -> None:
    root = kit.vbox(spacing=14, margins=(20, 16, 20, 6))
    content.setLayout(root)
    ...  # place every required part, see below

def extra_qss(t: Theme, m: Metrics, glyph: GlyphFile) -> str:
    return f"QFrame#hero {{ background: {t.accent_soft}; }}"

VARIANT = Variant(
    key="<key>", name="Shown in the menu", description="One sentence for the tooltip.",
    light=LIGHT, dark=DARK, metrics=Metrics(...), build_layout=build,
    extra_qss=extra_qss,          # optional
    after_refresh=None,           # optional: (window, theme) -> None, e.g. recolour icons
    scheme=None,                  # None = follow the system; "dark"/"light" = always that
)
```

Register the module name in `VARIANT_MODULES` in `__init__.py` (menu order).

## Parts you must place (attributes of `w`)

| Part | What it is |
|------|------------|
| `title_label` | App name (objectName `appTitle`) |
| `settings_button` | “Settings” menu button (objectName `menuButton`) |
| `url_edit` | Multi-line link box (objectName `links`) |
| `paste_btn`, `clear_links_btn` | Paste from clipboard / empty the box (`clear_links_btn` is `flat`) |
| `preset_combo` | Preset dropdown |
| `folder_edit`, `browse_btn`, `open_folder_btn` | Save-to folder field, Browse…, Open |
| `playlist_check`, `subs_check`, `meta_check` | The three option checkboxes |
| `advanced_toggle`, `advanced_panel` | “Advanced options” toggle and the panel it shows/hides |
| `download_btn`, `cancel_btn` | Primary action (`primary`) and “Cancel all” (`secondary`) |
| `summary_label` | “2 downloading · 1 done” (`muted`, may be empty) |
| `clear_finished_btn` | “Clear finished” (`flat`) |
| `queue` | The downloads list (objectName `queue`) — give it the stretch |
| `banner` | Error message with fix buttons; hidden until a download fails |
| `log_toggle`, `log_view` | “Log” toggle and the raw yt-dlp output it shows/hides |
| `copy_log_btn`, `clear_log_btn` | Shown only while the log is open |

Optional (hidden if you leave them out): `logo_label` (app icon; `w.set_logo_size(px)`),
`tagline_label` (“Paste a link · pick a preset · click Download”), `preset_hint` (one-line
description of the selected preset — strongly recommended, it makes presets understandable).

The advanced panel arranges its own fields. Side-by-side by default — line its labels up
with yours via `kit.align_labels([*your_labels, *w.advanced_panel.labels])`. In narrow
places (sidebars) call `w.advanced_panel.set_stacked(True)` to put labels above fields.

## Rules

- Place every required part exactly once. Parts left out are hidden and reported.
- You may: set sizes (`setMinimumWidth`, `setMaximumHeight`, `setFixedWidth`…), size policies,
  contents margins, icon sizes, `w.set_logo_size()`, queue column widths
  (`w.queue.setColumnWidth`), and tag parts for your stylesheet with `kit.set_role(part, "x")`
  → `QPushButton[role="x"] { … }`. All of these are reset automatically before the next
  variant is applied.
- You may not: change texts, placeholders, objectNames, signals, visibility (`show/hide`),
  hide queue columns, or create your own inputs/buttons. Make your own decorative widgets
  freely (labels, frames, step numbers…) — they are children of `content` and are deleted
  when the variant changes.
- Don't keep references to anything between calls; `build` may run many times.
- Style with `extra_qss`, targeting objectNames (`#primary`, `#card`, `#links`, `#queue`,
  `#menuButton`, `#fieldLabel`, `#cardTitle`, `#appTitle`…) or your own frame names
  (`kit.panel(content, "hero")`). Use the `Theme` colours, never hard-coded colours that
  ignore dark mode (unless the variant forces a scheme). The base stylesheet is in
  `ui/theme.py` — read it to see what you are overriding.
- Stylesheet images: `glyph(name, "#rrggbb")` returns a PNG path for `url("…")`; glyph
  names are in `ui/icon.py` (`chevron`, `chevron-right`, `check`, `folder`, `download`…).
- Fonts: list fallbacks for both systems, e.g. `("Segoe UI Variable Text", "Segoe UI",
  "Noto Sans", "DejaVu Sans")`. Body size goes in `Metrics.font_size` (pt).
- The layout must work from 760×560 (minimum window) up to full screen; the page scrolls
  vertically if needed but must never need horizontal scrolling.
- Keep it intuitive for non-technical people: the order *paste → choose → download* must be
  obvious, and the Download button must be the most prominent control.

## Checking your work

```bash
python tools/screenshots.py --variant <key> --check --out screenshots/<key>
```

renders light/dark × {demo, advanced+log open, 820×620, empty} into PNGs and prints
automatic problems (unplaced parts, squeezed text, overlaps, cut-offs). Look at every
image. `python tools/screenshots.py --switch-test` switches through all variants live.
