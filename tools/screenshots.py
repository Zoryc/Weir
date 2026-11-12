"""Render and check the main window for UI variants (developer tool).

Run from the project root:

    python tools/screenshots.py                          # every variant -> screenshots/
    python tools/screenshots.py --variant compact        # one variant (repeatable)
    python tools/screenshots.py --variant compact --out C:/tmp/shots
    python tools/screenshots.py --sheet                  # + contact-sheet.png of all rendered variants
    python tools/screenshots.py --check                  # also print automatic layout problems
    python tools/screenshots.py --switch-test            # switch through every variant live, report problems

Images per variant (variants that force light or dark only get that scheme):
    <key>-light.png / <key>-dark.png                  1040x820, demo downloads
    <key>-light-advanced.png / <key>-dark-advanced.png  Advanced options + Log open
    <key>-light-small.png / <key>-dark-small.png      820x620 (small laptop)
    <key>-light-empty.png / <key>-dark-empty.png      nothing queued yet

The window is never shown on screen. Settings go to a throw-away folder.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRect, QStandardPaths, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QWidget,
)

from core import APP_NAME  # noqa: E402

SIZE_DEFAULT = (1040, 820)
SIZE_SMALL = (820, 620)


def _settle(app: QApplication, rounds: int = 8) -> None:
    for _ in range(rounds):
        app.processEvents()
        time.sleep(0.02)


def _populate_demo(win) -> None:  # noqa: ANN001 - MainWindow, imported lazily
    from core.downloader import DownloadJob, DownloadOptions, JobStatus
    from core.errors import explain_error

    _clear(win)
    win.url_edit.add_links([
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/playlist?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG",
    ])
    p = win.presets.builtin
    opts = DownloadOptions(str(Path.home() / "Downloads"), False, False, True, [], "%(title)s.%(ext)s", "", "")
    demo = [
        ("Lofi beats to study to – 1 hour mix 🎧", p[4], JobStatus.DOWNLOADING, 63.4, "4.21MiB/s", "00:12", []),
        ("東京の夜景 4K ドローン映像", p[3], JobStatus.CONVERTING, 100.0, "", "", []),
        ("Café de Flore — live session", p[0], JobStatus.DONE, 100.0, "", "",
         [str(Path.home() / "Downloads" / "Café de Flore — live session.mp4")]),
        ("", p[1], JobStatus.ERROR, 0.0, "", "", []),
        ("", p[2], JobStatus.WAITING, 0.0, "", "", []),
    ]
    for i, (title, preset, status, pct, speed, eta, files) in enumerate(demo, start=1):
        job = DownloadJob(id=900 + i, url=f"https://www.youtube.com/watch?v=demo{i}", preset=preset, options=opts)
        job.title, job.status, job.percent, job.speed, job.eta, job.files = title, status, pct, speed, eta, files
        if status == JobStatus.ERROR:
            job.error = explain_error(["ERROR: [youtube] demo4: Sign in to confirm you're not a bot."],
                                      has_js_runtime=True)
        win.manager._jobs[job.id] = job
        win.manager.job_added.emit(job.id)
        if status == JobStatus.ERROR:
            win.manager.job_failed.emit(job.id)
    win.queue.scrollToTop()
    win._refresh_summary()


def _clear(win) -> None:  # noqa: ANN001
    win.manager._jobs.clear()
    win.queue.clear()
    win._items.clear()
    win.banner.hide()
    win.url_edit.clear()
    win._refresh_summary()


# --------------------------------------------------------------------- checks

def _interactive(win) -> list[tuple[str, QWidget]]:  # noqa: ANN001
    skip = {"queue", "log_view", "advanced_panel", "url_edit", "banner"}
    found = [(name, w) for name, w in win.parts.items() if name not in skip and w.isVisible()]
    panel = win.advanced_panel
    if panel.isVisible():
        for attr in ("extra_args_edit", "template_edit", "template_reset_btn", "cookies_combo",
                     "rate_edit", "parallel_combo", "save_preset_btn", "delete_preset_btn"):
            found.append((f"advanced.{attr}", getattr(panel, attr)))
    return found


def _global_rect(widget: QWidget) -> QRect:
    top_left = widget.mapTo(widget.window(), widget.rect().topLeft())
    return QRect(top_left, widget.size())


def check_layout(win, label: str) -> list[str]:  # noqa: ANN001
    """Automatic problems: unplaced parts, zero sizes, squeezed text, overlaps, cut-offs."""
    from ui.main_window import REQUIRED_PARTS

    import shiboken6
    from PySide6.QtCore import QObject

    issues: list[str] = []
    for attr, value in vars(win).items():  # every Qt object the window keeps must survive relayouts
        if isinstance(value, QObject) and not shiboken6.isValid(value):
            issues.append(f"window.{attr}: Qt object was deleted by a relayout")
    content = win._page.widget()
    managed_hidden = {
        "advanced_panel": not win.advanced_toggle.isChecked(),
        "log_view": not win.log_toggle.isChecked(),
        "copy_log_btn": not win.log_toggle.isChecked(),
        "clear_log_btn": not win.log_toggle.isChecked(),
        "banner": True,
    }
    for name in REQUIRED_PARTS:
        widget = win.parts[name]
        if not content.isAncestorOf(widget):
            issues.append(f"{name}: not placed in the layout")
        elif not widget.isVisible() and not managed_hidden.get(name, False):
            issues.append(f"{name}: placed but not visible")

    page_width = win._page.viewport().width()
    widgets = _interactive(win)
    for name, widget in widgets:
        if isinstance(widget, QLabel) and not widget.text() and widget.pixmap().isNull():
            continue  # empty labels (e.g. no summary yet) may collapse
        if widget.width() <= 0 or widget.height() <= 0:
            issues.append(f"{name}: zero size")
            continue
        hint = widget.minimumSizeHint()
        squeezable = isinstance(widget, (QAbstractButton, QComboBox)) or (
            isinstance(widget, QLabel) and not widget.wordWrap()
        )
        if squeezable and hint.width() > 0 and widget.width() + 2 < hint.width():
            issues.append(f"{name}: squeezed to {widget.width()}px (needs {hint.width()}px) — text may be cut")
        if isinstance(widget, QLineEdit) and widget.width() < 60:
            issues.append(f"{name}: only {widget.width()}px wide")
        rect = _global_rect(widget)
        if rect.right() > win._page.mapTo(win, win._page.viewport().rect().topLeft()).x() + page_width + 1:
            issues.append(f"{name}: cut off on the right")
    for i, (name_a, a) in enumerate(widgets):
        ra = _global_rect(a)
        for name_b, b in widgets[i + 1:]:
            if a.isAncestorOf(b) or b.isAncestorOf(a):
                continue
            inter = ra.intersected(_global_rect(b))
            if inter.width() > 3 and inter.height() > 3:
                issues.append(f"{name_a} overlaps {name_b}")
    return [f"[{label}] {issue}" for issue in issues]


def part_state(win) -> dict[str, tuple]:  # noqa: ANN001
    """Everything about the shared parts a variant could leave behind when it is replaced."""
    from PySide6.QtWidgets import QPlainTextEdit

    state: dict[str, tuple] = {}
    for name, widget in win.parts.items():
        props = tuple(sorted(bytes(p.data()).decode() for p in widget.dynamicPropertyNames()
                             if not bytes(p.data()).startswith(b"_q_")))
        extra: tuple = ()
        if isinstance(widget, QLabel):
            extra = (int(widget.alignment().value), widget.wordWrap())
        elif isinstance(widget, QLineEdit):
            extra = (int(widget.alignment().value), len(widget.actions()))
        elif isinstance(widget, QPlainTextEdit):
            extra = (int(widget.lineWrapMode().value), widget.viewportMargins().top(),
                     int(widget.horizontalScrollBarPolicy().value), int(widget.verticalScrollBarPolicy().value))
        state[name] = (
            widget.font().toString(), widget.styleSheet(), widget.graphicsEffect() is not None,
            int(widget.layoutDirection().value), int(widget.focusPolicy().value),
            int(widget.cursor().shape().value), props, extra, widget.objectName(), widget.isWindow(),
        )
    header = win.queue.header()
    state["queue.columns"] = tuple(
        (win.queue.columnWidth(c), int(header.sectionResizeMode(c).value), win.queue.isColumnHidden(c))
        for c in range(win.queue.columnCount())
    )
    state["queue.view"] = (int(win.queue.horizontalScrollBarPolicy().value),
                           int(win.queue.verticalScrollBarPolicy().value), win.queue.indentation())
    state["preset_combo.contents"] = (win.preset_combo.minimumContentsLength(),)
    return state


def _exercise(app: QApplication, win) -> None:  # noqa: ANN001
    """Poke the window the way a user would, so stray event filters get a chance to misbehave."""
    for size in ((900, 700), SIZE_DEFAULT):
        win.resize(*size)
        _settle(app, 2)
    for expanded in (True, False):
        win.advanced_toggle.setChecked(expanded)
        win.log_toggle.setChecked(expanded)
        _settle(app, 2)
    for widget in (win.url_edit, win.folder_edit, win.preset_combo, win.url_edit):
        widget.setFocus()
        _settle(app, 1)
    for job in win.manager.jobs():
        win.manager.job_changed.emit(job.id)
    _settle(app, 2)


# ------------------------------------------------------------------- render

def render_variant(app: QApplication, tm, win, variant, out: Path, check: bool) -> list[str]:  # noqa: ANN001
    tm.set_variant(variant)
    schemes = [variant.scheme] if variant.scheme else ["light", "dark"]
    problems: list[str] = []
    for scheme in schemes:
        tm.force_scheme(scheme)
        shots = [
            ("", SIZE_DEFAULT, "demo", False),
            ("-advanced", SIZE_DEFAULT, "demo", True),
            ("-small", SIZE_SMALL, "demo", False),
            ("-empty", SIZE_DEFAULT, "empty", False),
        ]
        for suffix, size, state, expanded in shots:
            if state == "demo":
                _populate_demo(win)
            else:
                _clear(win)
            win.advanced_toggle.setChecked(expanded)
            win.log_toggle.setChecked(expanded)
            win.resize(*size)
            _settle(app)
            name = f"{variant.key}-{scheme}{suffix}.png"
            win.grab().save(str(out / name))
            if check:
                problems += check_layout(win, f"{variant.key} {scheme}{suffix or ''} {size[0]}x{size[1]}")
    tm.force_scheme(None)
    return problems


def contact_sheet(variants, out: Path) -> Path:  # noqa: ANN001
    thumb_w = 620
    pad = 24
    label_h = 54
    rows = []
    for v in variants:
        images = [out / f"{v.key}-{s}.png" for s in (["light", "dark"] if not v.scheme else [v.scheme])]
        images = [QImage(str(p)) for p in images if p.exists()]
        if images:
            rows.append((v, [img.scaledToWidth(thumb_w, Qt.TransformationMode.SmoothTransformation)
                             for img in images]))
    if not rows:
        raise SystemExit("Nothing rendered yet")
    thumb_h = max(img.height() for _, imgs in rows for img in imgs)
    width = pad + 2 * (thumb_w + pad)
    height = pad + len(rows) * (label_h + thumb_h + pad)
    sheet = QImage(width, height, QImage.Format.Format_RGB32)
    sheet.fill(QColor("#e9ebef"))
    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    title_font = QFont(QApplication.font())
    title_font.setPointSizeF(15)
    title_font.setBold(True)
    desc_font = QFont(QApplication.font())
    desc_font.setPointSizeF(10)
    y = pad
    for index, (v, imgs) in enumerate(rows, start=1):
        painter.setPen(QColor("#1d2129"))
        painter.setFont(title_font)
        painter.drawText(pad, y + 24, f"{index}. {v.name}")
        painter.setFont(desc_font)
        painter.setPen(QColor("#5f6672"))
        painter.drawText(pad, y + 46, v.description)
        x = pad
        for img in imgs:
            painter.drawImage(x, y + label_h, img)
            painter.setPen(QColor("#c9ced6"))
            painter.drawRect(x - 1, y + label_h - 1, img.width() + 1, img.height() + 1)
            x += thumb_w + pad
        y += label_h + thumb_h + pad
    painter.end()
    path = out / "contact-sheet.png"
    sheet.save(str(path))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", action="append", help="variant key (repeatable); default: all")
    parser.add_argument("--out", default=str(ROOT / "screenshots"), help="output folder")
    parser.add_argument("--sheet", action="store_true", help="also write contact-sheet.png")
    parser.add_argument("--check", action="store_true", help="print automatic layout problems")
    parser.add_argument("--switch-test", action="store_true", help="switch through all variants live")
    args = parser.parse_args()

    QGuiApplication.setApplicationName(f"{APP_NAME}-shots-{os.getpid()}")
    app = QApplication(sys.argv[:1])

    from core.deps import DepsManager
    from core.presets import PresetStore
    from core.settings import Settings
    from ui.main_window import MainWindow
    from ui.theme import ThemeManager
    from ui.variants import available_variants, get_variant

    variants = list(available_variants())
    if args.variant:
        wanted = set(args.variant)
        unknown = wanted - {v.key for v in variants}
        if unknown:
            print(f"Unknown or broken variant(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        variants = [v for v in variants if v.key in wanted]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    settings._normalize()
    settings.save_dir = str(Path.home() / "Downloads")
    tm = ThemeManager(app, get_variant("classic"))
    win = MainWindow(settings, PresetStore.load(), DepsManager(), tm)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(*SIZE_DEFAULT)
    win.show()
    _settle(app)

    problems: list[str] = []
    try:
        if args.switch_test:
            errors: list[str] = []
            previous_hook = sys.excepthook
            sys.excepthook = lambda t, e, tb: errors.append(f"{t.__name__}: {e}")  # Qt callbacks report here
            _populate_demo(win)
            first = available_variants()[0]
            tm.set_variant(first)
            _exercise(app, win)
            baseline = part_state(win)
            for v in list(available_variants()) * 2:  # twice: switching back must work too
                before = len(errors)
                tm.set_variant(v)
                for scheme in ("light", "dark"):
                    tm.force_scheme(scheme)
                    _settle(app, 3)
                    problems += check_layout(win, f"switch {v.key} {scheme}")
                _exercise(app, win)
                tm.set_variant(first)  # come back and compare: nothing may stay behind
                _exercise(app, win)
                after = part_state(win)
                for key, value in baseline.items():
                    if after.get(key) != value:
                        problems.append(f"[leak {v.key}] {key} differs after switching back: "
                                        f"{value!r} -> {after.get(key)!r}")
                problems += [f"[error {v.key}] {e}" for e in errors[before:]]
            tm.force_scheme(None)
            sys.excepthook = previous_hook
            print(f"switched through {len(available_variants())} variants twice "
                  f"(leak + error check against {first.key})")
        else:
            for v in variants:
                problems += render_variant(app, tm, win, v, out, args.check)
                print(f"rendered {v.key}", flush=True)
            if args.sheet:
                print("sheet:", contact_sheet(list(available_variants()), out))
    finally:
        import shiboken6

        win._force_close = True
        win.close()
        _settle(app, 2)
        shiboken6.delete(win)  # tear Qt down while Python is still fully alive
        _settle(app, 1)
        for location in (QStandardPaths.StandardLocation.AppConfigLocation,
                         QStandardPaths.StandardLocation.CacheLocation):
            folder = QStandardPaths.writableLocation(location)
            if folder and "-shots-" in folder:
                shutil.rmtree(folder, ignore_errors=True)

    if args.check or args.switch_test:
        if problems:
            print(f"\n{len(problems)} layout problem(s):")
            for problem in dict.fromkeys(problems):
                print("  " + problem)
        else:
            print("No layout problems found.")
    print(f"Images in {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
