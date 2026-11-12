"""Ytdlp Downloader — a simple desktop window for yt-dlp.

Run from source:  python main.py [links...]
"""

from __future__ import annotations

import sys
import traceback
from types import TracebackType

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from core import APP_DESKTOP_ID, APP_DISPLAY_NAME, APP_NAME, APP_VERSION
from core.deps import DepsManager
from core.paths import IS_WINDOWS, config_dir, sanitize_inherited_environment
from core.presets import PresetStore
from core.settings import Settings
from ui.icon import app_icon
from ui.main_window import MainWindow
from ui.theme import ThemeManager
from ui.variants import get_variant
from ui.widgets import extract_links_from_text


def _set_windows_app_id() -> None:
    """Group the taskbar button and notifications under our own name and icon."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.App")
    except (AttributeError, OSError):
        pass


def _install_excepthook() -> None:
    """Never fail silently: log unexpected errors and tell the user."""
    shown = False

    def hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        nonlocal shown
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        if sys.__stderr__ is not None:
            sys.__stderr__.write(text)
        log_path = config_dir() / "error.log"
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass
        if not shown and QApplication.instance() is not None:
            shown = True
            QMessageBox.critical(
                None, APP_DISPLAY_NAME,
                f"Something unexpected went wrong. Details were saved to:\n{log_path}\n\n{exc}",
            )

    sys.excepthook = hook


def main() -> int:
    sanitize_inherited_environment()
    _set_windows_app_id()
    QGuiApplication.setApplicationName(APP_NAME)
    QGuiApplication.setApplicationDisplayName(APP_DISPLAY_NAME)
    QGuiApplication.setApplicationVersion(APP_VERSION)
    QGuiApplication.setDesktopFileName(APP_DESKTOP_ID)

    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    _install_excepthook()

    settings = Settings.load()
    # Fusion + the chosen UI variant's palette and stylesheet; follows system light/dark.
    theme = ThemeManager(app, get_variant(settings.ui_variant))
    presets = PresetStore.load()
    deps = DepsManager()
    window = MainWindow(settings, presets, deps, theme)
    links = extract_links_from_text(" ".join(sys.argv[1:]))
    if links:
        window.url_edit.add_links(links)
    window.show()
    deps.refresh()  # version checks run in a background thread
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
