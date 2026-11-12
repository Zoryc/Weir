"""Render and check Weir's main window off-screen (developer tool).

Run from the project root:

    python tools/screenshots.py                    # -> screenshots/
    python tools/screenshots.py --check            # also print automatic layout problems
    python tools/screenshots.py --out C:/tmp/shots

Images, for light and dark mode:
    weir-<scheme>.png            1040x820, demo downloads
    weir-<scheme>-advanced.png   Advanced options + Log open
    weir-<scheme>-small.png      820x620 (small laptop)
    weir-<scheme>-empty.png      nothing queued yet
    weir-<scheme>-laptop.png     1280x680 (1366x768 laptop, or 1080p at 150 %), as the
                                 window scrolled itself after the demo failure

--check reports controls missing from the layout, squeezed or cut-off text and
overlapping controls, and, at 1280x680, an error banner or first queue row left
outside the visible part of the page. The window is never shown on screen;
settings go to a throw-away folder.
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
from PySide6.QtGui import QGuiApplication  # noqa: E402
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
# A neutral folder for the pictures (they end up in the README; no real user names in them).
DEMO_FOLDER = "C:\\Users\\you\\Downloads" if sys.platform == "win32" else "/home/you/Downloads"
SIZE_SMALL = (820, 620)
# What a 1366x768 laptop, or a 1080p screen at 150 %, leaves for the window.
SIZE_LAPTOP = (1280, 680)


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
    opts = DownloadOptions(DEMO_FOLDER, False, False, True, [], "%(title)s.%(ext)s", "", "")
    demo = [
        ("Lofi beats to study to – 1 hour mix 🎧", p[4], JobStatus.DOWNLOADING, 63.4, "4.21MiB/s", "00:12", []),
        ("東京の夜景 4K ドローン映像", p[3], JobStatus.CONVERTING, 100.0, "", "", []),
        ("Café de Flore — live session", p[0], JobStatus.DONE, 100.0, "", "",
         [str(Path(DEMO_FOLDER) / "Café de Flore — live session.mp4")]),
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

    issues: list[str] = []
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


def check_in_view(win, label: str) -> list[str]:  # noqa: ANN001
    """The error banner and the queue's first row must be inside the visible part of the
    page, once the window has had the chance to scroll them into view itself."""
    issues: list[str] = []
    viewport = win._page.viewport()

    def outside(widget: QWidget, rect: QRect) -> str:
        top = widget.mapTo(viewport, rect.topLeft()).y()
        bottom = widget.mapTo(viewport, rect.bottomLeft()).y()
        if top < 0 or bottom >= viewport.height():
            return f"at page y {top}..{bottom}, visible part 0..{viewport.height() - 1}"
        return ""

    if not win.banner.isVisible():
        issues.append("banner: not shown after a failed download")
    elif where := outside(win.banner, win.banner.rect()):
        issues.append(f"banner: outside the visible page ({where})")
    item = win.queue.topLevelItem(0)
    if item is None:
        issues.append("queue: no rows to check")
    else:
        row = win.queue.visualItemRect(item)
        queue_view = win.queue.viewport()
        if row.top() < 0 or row.bottom() >= queue_view.height():
            issues.append("queue: first row scrolled out of the list")
        elif where := outside(queue_view, row):
            issues.append(f"queue: first row outside the visible page ({where})")
    return [f"[{label}] {issue}" for issue in issues]


def render(app: QApplication, tm, win, out: Path, check: bool) -> list[str]:  # noqa: ANN001
    schemes = [tm.design.scheme] if tm.design.scheme else ["light", "dark"]
    problems: list[str] = []
    for scheme in schemes:
        tm.force_scheme(scheme)
        shots = [
            ("", SIZE_DEFAULT, "demo", False),
            ("-advanced", SIZE_DEFAULT, "demo", True),
            ("-small", SIZE_SMALL, "demo", False),
            ("-empty", SIZE_DEFAULT, "empty", False),
            ("-laptop", SIZE_LAPTOP, "demo", False),
        ]
        for suffix, size, state, expanded in shots:
            if state == "demo":
                _populate_demo(win)  # its failed download makes the window scroll to the banner
            else:
                _clear(win)
            win.advanced_toggle.setChecked(expanded)
            win.log_toggle.setChecked(expanded)
            win.resize(*size)
            _settle(app)
            laptop = suffix == "-laptop"
            if not laptop:
                win._page.verticalScrollBar().setValue(0)  # README pictures show the top
                _settle(app, 2)
            win.grab().save(str(out / f"weir-{scheme}{suffix}.png"))
            if check:
                label = f"{scheme}{suffix} {size[0]}x{size[1]}"
                problems += check_layout(win, label)
                if laptop:
                    problems += check_in_view(win, label)
    tm.force_scheme(None)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "screenshots"), help="output folder")
    parser.add_argument("--check", action="store_true", help="print automatic layout problems")
    args = parser.parse_args()

    QGuiApplication.setApplicationName(f"{APP_NAME}-shots-{os.getpid()}")
    app = QApplication(sys.argv[:1])

    import shiboken6

    from core.deps import DepsManager
    from core.presets import PresetStore
    from core.settings import Settings
    from ui.design import DESIGN
    from ui.main_window import MainWindow
    from ui.theme import ThemeManager

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    settings._normalize()
    settings.save_dir = DEMO_FOLDER
    tm = ThemeManager(app, DESIGN)
    win = MainWindow(settings, PresetStore.load(), DepsManager(), tm)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(*SIZE_DEFAULT)
    win.show()
    _settle(app)

    try:
        problems = render(app, tm, win, out, args.check)
    finally:
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

    if args.check:
        if problems:
            print(f"{len(problems)} layout problem(s):")
            for problem in dict.fromkeys(problems):
                print("  " + problem)
        else:
            print("No layout problems found.")
    print(f"Images in {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
